"""ES↔PG 对账（T14）：PG 权威、ES 派生。检测并修复漂移（report + repair）。

漂移类：
- missing        PG active 行存在、ES 无 doc            → re-embed + 重发 outbox `index`（原版本，不调 ingest）
- version_drift  PG active chunk、ES 有 doc 但 chunk_version≠active → 同 missing（覆盖重发）
- avail_drift    PG available=1、ES available_int=0     → outbox `set_available` available=1
- retired_leak   ES available_int=1 但属 PG 已退役版本    → outbox `set_available` available=0（保到 GC）
- orphan         ES 有 doc、PG 无此 id（任何版本）        → outbox `delete`

幂等：再跑一次全 0、无新事件。reconcile 绝不调 ingest_file（避免 ES 抖动时版本无限增长）。
"""
import json

from app.adapters import embedder
from app.config import settings
from app.db import get_conn
from app.es import INDEX, get_es, scan_all
from app.ingest.pipeline import build_chunk_source, build_summary_source
from app.retrieval import simhash


def _es_get(es, doc_id: str):
    """返回 source dict 或 None（缺失）。兼容真 ES(NotFoundError) 与 FakeES(空 _source)。"""
    try:
        src = es.get(index=INDEX, id=doc_id)["_source"]
    except Exception:  # noqa: BLE001  真 ES NotFoundError；其它异常按缺失处理让对账修复
        return None
    return src or None


def _resolve_files(tenant_id: str, file_id: str | None) -> tuple[list[str], str | None]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if file_id:
                cur.execute("SELECT tenant_id FROM kb_file WHERE id=%s", (file_id,))
                row = cur.fetchone()
                if not row or str(row[0]) != str(tenant_id):
                    return [], "KB_FILE_NOT_FOUND"
                return [file_id], None
            cur.execute("SELECT id FROM kb_file WHERE tenant_id=%s ORDER BY created_at", (tenant_id,))
            return [str(r[0]) for r in cur.fetchall()], None


def _load_pg(cur, fid: str, tenant_id: str) -> dict:
    """一次读全该文件 PG 状态：active 版本、active 行、全量 id 集、重建所需的行内容。"""
    cur.execute(
        "SELECT active_chunk_version, active_summary_version FROM kb_file WHERE id=%s AND tenant_id=%s",
        (fid, tenant_id),
    )
    fp = cur.fetchone()
    if not fp:
        return {}
    active_chunk, active_summary = fp[0], fp[1]

    cur.execute(
        "SELECT id, content, page_num, chunk_order, chunk_version, simhash, available FROM kb_chunk "
        "WHERE file_id=%s AND tenant_id=%s",
        (fid, tenant_id),
    )
    chunks = cur.fetchall()
    cur.execute(
        "SELECT id, summary_text, source_chunk_ids, coverage_ratio, summary_version FROM kb_summary_doc "
        "WHERE file_id=%s AND tenant_id=%s",
        (fid, tenant_id),
    )
    summaries = cur.fetchall()
    return {
        "active_chunk": active_chunk,
        "active_summary": active_summary,
        "chunks": chunks,  # 全部版本
        "summaries": summaries,
    }


def _classify(fid: str, tenant_id: str, pg: dict, es) -> dict:
    """读 ES + 扫描 → 漂移分类（纯读）。"""
    active_chunk = pg["active_chunk"]
    active_summary = pg["active_summary"]

    active_chunks = {str(r[0]): r for r in pg["chunks"] if r[4] == active_chunk}  # id→row
    active_summaries = {str(r[0]): r for r in pg["summaries"] if r[4] == active_summary}
    all_chunk_ids = {str(r[0]) for r in pg["chunks"]}
    all_summary_ids = {str(r[0]) for r in pg["summaries"]}

    miss_chunk, drift_chunk, avail = [], [], []
    for cid, row in active_chunks.items():
        src = _es_get(es, cid)
        if src is None:
            miss_chunk.append(cid)
        elif src.get("chunk_version_int") != active_chunk:
            drift_chunk.append(cid)
        elif src.get("available_int") != 1:
            avail.append(cid)  # PG available=1（active）、ES available_int=0

    miss_summary = []
    for sid in active_summaries:
        src = _es_get(es, sid)
        if src is None:
            miss_summary.append(sid)
        elif src.get("available_int") != 1:
            avail.append(sid)

    # ES 扫描：可见但非 active 的 doc = 泄漏（retired→隐藏 / orphan→删）
    retired_leak, orphan = [], []
    for did, src in scan_all(es, INDEX, source_fields=["doc_type_kwd", "chunk_version_int", "available_int",
                                                        "file_id_kwd", "tenant_id_kwd"]):
        if src.get("tenant_id_kwd") != str(tenant_id) or src.get("file_id_kwd") != str(fid):
            continue
        if did in active_chunks or did in active_summaries:
            continue  # active，上面已处理
        if src.get("available_int") != 1:
            continue  # 已隐藏的退役/孤儿，留给 GC
        if did in all_chunk_ids or did in all_summary_ids:
            retired_leak.append(did)  # PG 有但旧版本泄漏可见 → 隐藏
        else:
            orphan.append(did)  # PG 全无 → 删

    return {
        "missing_chunks": miss_chunk,
        "version_drift_chunks": drift_chunk,
        "missing_summaries": miss_summary,
        "avail_drift": avail,
        "retired_leak": retired_leak,
        "orphan": orphan,
        "active_chunks": active_chunks,
        "active_summaries": active_summaries,
    }


def reconcile(
    tenant_id: str,
    file_id: str | None = None,
    dry_run: bool = True,
    repair: bool = True,
    principal_user_id: str | None = None,
) -> dict:
    """扫描→分类→(dry_run: 仅报告；apply & repair: 发 outbox + drain)。"""
    from app.indexing import relay

    files, err = _resolve_files(tenant_id, file_id)
    if err:
        return {"dry_run": dry_run, "error": err, "files_scanned": 0, "drift": {}, "details": []}

    es = get_es()
    drift_agg = {k: 0 for k in (
        "missing", "version_drift", "avail_drift", "retired_leak", "orphan")}
    details: list[dict] = []

    for fid in files:
        with get_conn() as conn:
            with conn.cursor() as cur:
                pg = _load_pg(cur, fid, tenant_id)
        if not pg:
            continue
        cls = _classify(fid, tenant_id, pg, es)
        miss_c, drift_c, miss_s = cls["missing_chunks"], cls["version_drift_chunks"], cls["missing_summaries"]
        avail, retired, orph = cls["avail_drift"], cls["retired_leak"], cls["orphan"]
        active_chunks, active_summaries = cls["active_chunks"], cls["active_summaries"]

        d = {
            "missing": len(miss_c) + len(miss_s),
            "version_drift": len(drift_c),
            "avail_drift": len(avail),
            "retired_leak": len(retired),
            "orphan": len(orph),
        }
        for k, v in d.items():
            drift_agg[k] += v
        details.append({"file_id": fid, **d, "dry_run": dry_run})

        if dry_run or not repair:
            continue

        # ===== apply & repair：原版本 re-embed 重发（chunk/summary 各批量） =====
        republish = []  # [(id, source)]
        rebuild_c_ids = miss_c + drift_c
        if rebuild_c_ids:
            rows = [active_chunks[i] for i in rebuild_c_ids if i in active_chunks]
            # drift_chunk 的 active_chunks 可能不含（版本≠active 的极端），退而从全量补读
            if len(rows) < len(rebuild_c_ids):
                have = {r[0] for r in rows}
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT id, content, page_num, chunk_order, chunk_version, simhash, available "
                            "FROM kb_chunk WHERE file_id=%s AND tenant_id=%s AND id = ANY(%s)",
                            (fid, tenant_id, [i for i in rebuild_c_ids if str(i) not in have]),
                        )
                        rows += cur.fetchall()
            vecs = embedder.embed_batch([r[1] for r in rows])
            for r, vec in zip(rows, vecs):
                chunk_dict = {
                    "content": r[1], "page": r[2], "chunk_order": r[3],
                    "simhash": simhash.to_unsigned(r[5]) if r[5] is not None else 0,
                }
                source = build_chunk_source(chunk_dict, vec, fid, tenant_id, r[4])
                source["available_int"] = 1  # active 重发须可见（build_*_source 默认 0 是 staging）
                republish.append((str(r[0]), source))
        if miss_s:
            rows = [active_summaries[i] for i in miss_s if i in active_summaries]
            if len(rows) < len(miss_s):
                have = {r[0] for r in rows}
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT id, summary_text, source_chunk_ids, coverage_ratio, summary_version "
                            "FROM kb_summary_doc WHERE file_id=%s AND tenant_id=%s AND id = ANY(%s)",
                            (fid, tenant_id, [i for i in miss_s if str(i) not in have]),
                        )
                        rows += cur.fetchall()
            rows = [r for r in rows if r[1]]  # 过滤 summary_text 为空的退化行
            vecs = embedder.embed_batch([r[1] for r in rows])
            for r, vec in zip(rows, vecs):
                it = {"summary_text": r[1], "source_chunk_ids": r[2]}
                source = build_summary_source(it, vec, fid, tenant_id, r[3] or 0.0)
                source["available_int"] = 1  # active 重发须可见
                republish.append((str(r[0]), source))

        # ===== 单事务写 outbox + 内联审计，commit 后 drain =====
        with get_conn() as conn:
            with conn.cursor() as cur:
                for doc_id, source in republish:
                    cur.execute(
                        "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'index',%s)",
                        (fid, json.dumps({"id": doc_id, "source": source})),
                    )
                if avail:
                    cur.execute(
                        "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'set_available',%s)",
                        (fid, json.dumps({"ids": avail, "available": 1})),
                    )
                if retired:
                    cur.execute(
                        "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'set_available',%s)",
                        (fid, json.dumps({"ids": retired, "available": 0})),
                    )
                if orph:
                    cur.execute(
                        "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'delete',%s)",
                        (fid, json.dumps({"ids": orph, "reason": "reconcile_orphan"})),
                    )
                detail = {"file_id": fid, "republished": len(republish), "set_avail_1": len(avail),
                          "set_avail_0": len(retired), "deleted": len(orph), "dry_run": False}
                cur.execute(
                    "INSERT INTO kb_audit_log(tenant_id,user_id,action,detail,result) "
                    "VALUES (%s,%s,'reconcile_repair',%s::jsonb,'ok')",
                    (tenant_id, principal_user_id, json.dumps(detail)),
                )
        relay.drain(fid)

    return {
        "dry_run": dry_run,
        "repair": repair,
        "files_scanned": len(files),
        "drift": drift_agg,
        "details": details,
    }

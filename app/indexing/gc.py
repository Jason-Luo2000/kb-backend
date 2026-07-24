"""版本级 GC / purge + outbox 修剪（T14）。

每次 ingest（T11 全量 / T12 增量）都留下一整代旧版本：旧 kb_chunk 翻 available=0（行仍在）、
旧 kb_summary_doc/kb_anchor 在 PG 从不动（仅靠版本谓词隐藏）、kb_version 每次+1、ES 旧 doc 翻
available_int=0 永不删、outbox published 行永不清。旧版本已被版本栅栏 + available=0 flip 隐藏，
**不影响可见性/正确性**，GC 是空间回收。

设计要点（见 plan）：
- 保留窗：purge `*_version < active - retain + 1`（默认 retain=1，回滚未实现只保当前）。
- 整版本删除，顺序 anchor→summary_doc→chunk→version（删 summary_doc 会 CASCADE 锚点，先删锚点避免双重计数）。
- ES 删除经 outbox `delete` 事件（与 PG 删除同事务写，commit 后 drain）；旧 doc 已不可见故零检索影响。
- 审计内联同事务（不调 audit()，其独立连接非原子）。
- 与在途 ingest 天然行不相交（ingest 读 active / 暂存 target / GC 清 <active），再加 advisory lock + pending_count 双保险。
"""
import json
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db import get_conn

DELETE_BATCH = 500


def _lockstep_ok(f) -> bool:
    a = f["active_doc_version"]
    return (
        a is not None
        and f["active_chunk_version"] == a
        and f["active_summary_version"] == a
        and f["active_anchor_version"] == a
    )


def _resolve_files(tenant_id: str, file_id: str | None) -> tuple[list[str], str | None]:
    """返回 (file_ids, error)。单文件时校验租户归属（跨租户/不存在→error）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if file_id:
                cur.execute(
                    "SELECT tenant_id FROM kb_file WHERE id=%s",
                    (file_id,),
                )
                row = cur.fetchone()
                if not row or str(row[0]) != str(tenant_id):
                    return [], "KB_FILE_NOT_FOUND"  # 跨租户/不存在：不泄漏存在性
                return [file_id], None
            cur.execute("SELECT id FROM kb_file WHERE tenant_id=%s ORDER BY created_at", (tenant_id,))
            return [str(r[0]) for r in cur.fetchall()], None


def purge_versions(
    tenant_id: str,
    file_id: str | None = None,
    dry_run: bool = True,
    retain: int | None = None,
    principal_user_id: str | None = None,
) -> dict:
    """purge 旧版本（PG 四表 + ES doc + outbox delete 事件）。

    dry_run=True：只 SELECT projected 计数，不写。
    dry_run=False：每文件独立短事务删除 + 内联审计，commit 后 drain。
    返回 {dry_run, retain, scanned, skipped:[{file_id,reason}], purged:{...}, details:[...]}。
    """
    from psycopg.rows import dict_row

    from app.indexing import relay

    retain = settings.gc_retain_versions if retain is None else retain
    files, err = _resolve_files(tenant_id, file_id)
    if err:
        return {"dry_run": dry_run, "retain": retain, "error": err, "scanned": 0, "purged": {}, "details": []}

    agg = {"chunks": 0, "summaries": 0, "anchors": 0, "versions": 0, "es_delete_events": 0}
    skipped: list[dict] = []
    details: list[dict] = []

    for fid in files:
        # dry_run：纯读，投影计数
        if dry_run:
            with get_conn() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """SELECT active_doc_version, active_chunk_version, active_summary_version,
                           active_anchor_version FROM kb_file WHERE id=%s AND tenant_id=%s""",
                        (fid, tenant_id),
                    )
                    f = cur.fetchone()
                    if not f or not _lockstep_ok(f):
                        skipped.append({"file_id": fid, "reason": "desync_or_missing"})
                        continue
                    threshold = f["active_chunk_version"] - retain + 1
                    if threshold <= 1:
                        details.append({"file_id": fid, "threshold": threshold, "projected": {k: 0 for k in agg}})
                        continue
                    proj = _count_purgeable(cur, fid, tenant_id, threshold)
                    for k, v in proj.items():
                        agg[k] += v
                    details.append({"file_id": fid, "threshold": threshold, "projected": proj})
            continue

        # apply：advisory lock + FOR UPDATE + 删除 + outbox + 内联审计，同事务
        with get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS got", (fid,))
                if not cur.fetchone()["got"]:
                    skipped.append({"file_id": fid, "reason": "advisory_lock_busy"})
                    continue  # 在途 ingest 持锁，跳过（rollback 空事务）
                if relay.pending_count(fid) > 0:
                    skipped.append({"file_id": fid, "reason": "outbox_pending"})
                    continue
                cur.execute(
                    """SELECT active_doc_version, active_chunk_version, active_summary_version,
                       active_anchor_version FROM kb_file WHERE id=%s AND tenant_id=%s FOR UPDATE""",
                    (fid, tenant_id),
                )
                f = cur.fetchone()
                if not f or not _lockstep_ok(f):
                    skipped.append({"file_id": fid, "reason": "desync_or_missing"})
                    continue
                threshold = f["active_chunk_version"] - retain + 1
                if threshold <= 1:
                    continue
                counts = _delete_purgeable(cur, fid, tenant_id, threshold)
                es_ids = counts["chunk_ids"] + counts["summary_ids"]
                for i in range(0, len(es_ids), DELETE_BATCH):
                    batch = es_ids[i : i + DELETE_BATCH]
                    cur.execute(
                        "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'delete',%s)",
                        (fid, json.dumps({"ids": batch, "reason": "gc_purge", "version": f["active_chunk_version"]})),
                    )
                    agg["es_delete_events"] += 1
                detail = {
                    "file_id": fid,
                    "retain": retain,
                    "threshold": threshold,
                    "chunks": counts["chunks"],
                    "summaries": counts["summaries"],
                    "anchors": counts["anchors"],
                    "versions": counts["versions"],
                    "dry_run": False,
                }
                cur.execute(
                    "INSERT INTO kb_audit_log(tenant_id,user_id,action,detail,result) VALUES (%s,%s,'gc_purge',%s::jsonb,'ok')",
                    (tenant_id, principal_user_id, json.dumps(detail)),
                )
                agg["chunks"] += counts["chunks"]
                agg["summaries"] += counts["summaries"]
                agg["anchors"] += counts["anchors"]
                agg["versions"] += counts["versions"]
                details.append(detail)
        # commit 后 drain：把 ES delete 事件发布出去
        relay.drain(fid)

    return {
        "dry_run": dry_run,
        "retain": retain,
        "scanned": len(files),
        "skipped": skipped,
        "purged": agg,
        "details": details,
    }


def _count_purgeable(cur, fid: str, tenant_id: str, threshold: int) -> dict:
    cur.execute(
        "SELECT count(*) FROM kb_anchor WHERE file_id=%s AND anchor_version<%s",
        (fid, threshold),
    )
    anchors = cur.fetchone()["count"]
    cur.execute(
        "SELECT count(*) FROM kb_summary_doc WHERE file_id=%s AND tenant_id=%s AND summary_version<%s",
        (fid, tenant_id, threshold),
    )
    summaries = cur.fetchone()["count"]
    cur.execute(
        "SELECT count(*) FROM kb_chunk WHERE file_id=%s AND tenant_id=%s AND chunk_version<%s",
        (fid, tenant_id, threshold),
    )
    chunks = cur.fetchone()["count"]
    cur.execute(
        "SELECT count(*) FROM kb_version WHERE file_id=%s AND doc_version<%s",
        (fid, threshold),
    )
    versions = cur.fetchone()["count"]
    return {"chunks": chunks, "summaries": summaries, "anchors": anchors, "versions": versions}


def _delete_purgeable(cur, fid: str, tenant_id: str, threshold: int) -> dict:
    # 1. 锚点（先删，避免 summary_doc CASCADE 再删一次造成 RETURNING 计数失真）。kb_anchor 无 tenant_id，靠 file_id 定位
    cur.execute(
        "DELETE FROM kb_anchor WHERE file_id=%s AND anchor_version<%s",
        (fid, threshold),
    )
    anchors = cur.rowcount
    # 2. summary_doc（CASCADE 锚点此时已无）；RETURNING id 供 ES 删除（summary 无 ES 版本字段）
    cur.execute(
        "DELETE FROM kb_summary_doc WHERE file_id=%s AND tenant_id=%s AND summary_version<%s RETURNING id",
        (fid, tenant_id, threshold),
    )
    summary_ids = [str(r["id"]) for r in cur.fetchall()]
    # 3. chunk
    cur.execute(
        "DELETE FROM kb_chunk WHERE file_id=%s AND tenant_id=%s AND chunk_version<%s RETURNING id",
        (fid, tenant_id, threshold),
    )
    chunk_ids = [str(r["id"]) for r in cur.fetchall()]
    # 4. version
    cur.execute(
        "DELETE FROM kb_version WHERE file_id=%s AND doc_version<%s",
        (fid, threshold),
    )
    versions = cur.rowcount
    return {
        "chunks": len(chunk_ids),
        "summaries": len(summary_ids),
        "anchors": anchors,
        "versions": versions,
        "chunk_ids": chunk_ids,
        "summary_ids": summary_ids,
    }


def prune_outbox(retain_days: int | None = None, principal_user_id: str | None = None) -> dict:
    """删 published_at 早于 retain_days 的 outbox 行（保 pending/failed 供取证）。"""
    retain_days = settings.outbox_retain_days if retain_days is None else retain_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kb_outbox WHERE published_at IS NOT NULL AND published_at < %s",
                (cutoff,),
            )
            deleted = cur.rowcount
            cur.execute(
                "INSERT INTO kb_audit_log(user_id,action,detail,result) VALUES (%s,'gc_prune_outbox',%s::jsonb,'ok')",
                (principal_user_id, json.dumps({"deleted": deleted, "retain_days": retain_days})),
            )
    return {"deleted": deleted, "retain_days": retain_days}

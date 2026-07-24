"""端到端摄取管线（T11 版本化原子发布 + T12 增量更新）。

T11：stage→drain→flip（PG+outbox 同事务暂存 → relay → 单事务 flip 四 active 指针）。
T12：target>1 且 changed_ratio≤min_changed_ratio 时走增量——未变 chunk 复用旧 embedding、
全匹配 window 复用旧 summary（仅重算变更部分）；否则全量。评审 #11/#22/#25/#28/#12/#23/#26。
"""
import hashlib
import json
import uuid

from app.adapters import embedder, parser
from app.config import settings
from app.db import get_conn
from app.ingest import chunker, diff, summarizer
from app.retrieval import simhash
from app.storage import get_minio

NAMESPACE = uuid.UUID("7b3a2c1e-5d4f-4a8b-9c6e-1f2d3a4b5c6d")


def _chunk_id(file_id: str, doc_version: int, order: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{file_id}:{doc_version}:{order}"))


def _read_file_bytes(storage_key: str) -> bytes:
    resp = get_minio().get_object(settings.minio_bucket, storage_key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def _chunk_source(chunk, vector, file_id, tenant_id, version) -> dict:
    return {
        "content_tks": chunk["content"],
        "q_vec_vec": vector,
        "file_id_kwd": file_id,
        "tenant_id_kwd": tenant_id,
        "sensitivity_int": 0,
        "doc_type_kwd": "chunk",
        "page_num_int": chunk["page"],
        "chunk_order_int": chunk["chunk_order"],
        "chunk_version_int": version,
        "simhash_long": simhash.to_signed(chunk["simhash"]),
        "available_int": 0,  # 暂存；flip 后 set_available=1
    }


def _summary_source(it, vector, file_id, tenant_id, coverage_ratio) -> dict:
    return {
        "content_tks": it["summary_text"],
        "q_vec_vec": vector,
        "file_id_kwd": file_id,
        "tenant_id_kwd": tenant_id,
        "sensitivity_int": 0,
        "doc_type_kwd": "summary",
        "is_summary_int": 1,
        "source_chunk_ids_kwd": [str(s) for s in it["source_chunk_ids"]],
        "coverage_ratio_f": coverage_ratio,
        "available_int": 0,
    }


def _new_chunks(file_id, target, blocks):
    chunks = []
    for c in chunker.chunk_blocks(blocks):
        cid = _chunk_id(file_id, target, c["chunk_order"])
        chunks.append(
            {
                **c,
                "chunk_id": cid,
                "content_hash": hashlib.sha256(c["content"].encode()).hexdigest(),
                "simhash": simhash.simhash(c["content"]),
            }
        )
    return chunks


def _build_sources(file_id, tenant_id, target, f, new_chunks):
    """返回 (chunk_sources, summary_items, metrics)。target>1 且小改走增量，否则全量。
    summary_item: {summary_text, source_chunk_ids, heading_path, _vec}。"""
    enabled = f.get("summary_enabled")
    incremental = False
    matched: dict = {}
    fresh: list = []
    if target > 1:
        prev = diff.prev_chunks(file_id, target - 1)
        matched, fresh = diff.match(new_chunks, prev)
        changed_ratio = (len(fresh) / len(new_chunks)) if new_chunks else 0.0
        incremental = changed_ratio <= settings.min_changed_ratio

    if not incremental:
        # 全量
        vectors = embedder.embed_batch([c["content"] for c in new_chunks])
        chunk_sources = [_chunk_source(c, vectors[i], file_id, tenant_id, target) for i, c in enumerate(new_chunks)]
        raw = summarizer.summarize_file(new_chunks) if enabled else []
        sum_vecs = embedder.embed_batch([it["summary_text"] for it in raw]) if raw else []
        summary_items = [{**it, "_vec": sum_vecs[i]} for i, it in enumerate(raw)]
        return chunk_sources, summary_items, {
            "mode": "full",
            "reused_chunks": 0,
            "fresh_chunks": len(new_chunks),
            "reused_summaries": 0,
            "fresh_summaries": len(raw),
        }

    # 增量：未变 chunk 复用旧 embedding（不调 embedder）
    fresh_vecs = iter(embedder.embed_batch([c["content"] for c in fresh]) if fresh else [])
    chunk_sources = []
    reused_chunks = 0
    for c in new_chunks:
        prev_row = matched.get(c["content_hash"])
        if prev_row:
            vec = diff.reuse_vector(prev_row["id"])
            reused_chunks += 1
        else:
            vec = next(fresh_vecs)
        chunk_sources.append(_chunk_source(c, vec, file_id, tenant_id, target))

    # summary：全匹配的旧 summary 复用（remap + 复用向量），fresh chunk 重新 summary
    old_to_new = diff.old_to_new_map(matched, new_chunks)
    summary_items = []
    reused_summaries = 0
    if enabled:
        for ps in diff.prev_summaries(file_id, target - 1):
            if ps["source_chunk_ids"] and all(s in old_to_new for s in ps["source_chunk_ids"]):
                summary_items.append(
                    {
                        "summary_text": ps["summary_text"],
                        "source_chunk_ids": [old_to_new[s] for s in ps["source_chunk_ids"]],
                        "heading_path": ps["heading_path"],
                        "_vec": diff.reuse_vector(ps["id"]),  # 复用旧 summary 向量，不调 LLM
                    }
                )
                reused_summaries += 1
        if fresh:
            fresh_items = summarizer.summarize_file(fresh)
            fresh_sum_vecs = embedder.embed_batch([it["summary_text"] for it in fresh_items]) if fresh_items else []
            for i, it in enumerate(fresh_items):
                summary_items.append({**it, "_vec": fresh_sum_vecs[i]})
    return chunk_sources, summary_items, {
        "mode": "incremental",
        "reused_chunks": reused_chunks,
        "fresh_chunks": len(new_chunks) - reused_chunks,
        "reused_summaries": reused_summaries,
        "fresh_summaries": len(summary_items) - reused_summaries,
    }


def ingest_file(file_id: str) -> dict:
    """stage（暂存+outbox）→ drain → flip（原子发布）。T12: target>1 小改走增量。返回统计。"""
    from psycopg.rows import dict_row

    from app.indexing import relay

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM kb_file WHERE id=%s", (file_id,))
            f = cur.fetchone()
            cur.execute("SELECT coalesce(max(doc_version),0) AS v FROM kb_version WHERE file_id=%s", (file_id,))
            target = int(cur.fetchone()["v"]) + 1  # 首摄=1，重建=N+1
    if not f:
        raise FileNotFoundError(file_id)
    tenant_id = str(f["tenant_id"])

    blocks = parser.parse_bytes(_read_file_bytes(f["storage_key"]), f.get("mime"), f["name"] or "")
    new_chunks = _new_chunks(file_id, target, blocks)
    chunk_sources, summary_items, metrics = _build_sources(file_id, tenant_id, target, f, new_chunks)

    covered = {cid for it in summary_items for cid in it["source_chunk_ids"]}
    coverage_ratio = (len(covered) / len(new_chunks)) if new_chunks else 0.0
    chunk_by_id = {c["chunk_id"]: c for c in new_chunks}
    chunk_ids = [c["chunk_id"] for c in new_chunks]

    # PG 事务 A —— 暂存（available=0）+ outbox 同事务（#11 原子）
    summary_ids: list[str] = []
    summary_sources: list[dict] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO kb_chunk(id,file_id,tenant_id,doc_version,chunk_order,content,content_ltks,
                   section_path,page_num,position,chunk_version,content_hash,simhash,sensitivity_level,available)
                   VALUES (%(id)s,%(fid)s,%(tid)s,%(dv)s,%(co)s,%(ct)s,%(ct)s,%(sp)s,%(pg)s,null,%(cv)s,%(ch)s,%(sh)s,0,0)""",
                [
                    {
                        "id": c["chunk_id"], "fid": file_id, "tid": tenant_id, "dv": target, "cv": target,
                        "co": c["chunk_order"], "ct": c["content"], "sp": c["section_path"],
                        "pg": c["page"], "ch": c["content_hash"], "sh": simhash.to_signed(c["simhash"]),
                    }
                    for c in new_chunks
                ],
            )
            for it in summary_items:
                sid = str(uuid.uuid4())
                summary_ids.append(sid)
                srcs = [str(s) for s in it["source_chunk_ids"]]
                tgt = srcs[0] if srcs else None
                tgt_content = chunk_by_id.get(tgt, {}).get("content", "") if tgt else ""
                cur.execute(
                    """INSERT INTO kb_summary_doc(id,file_id,tenant_id,summary_type,heading_path,content_md,
                       summary_text,content_fingerprint,source_chunk_ids,coverage_ratio,summary_version)
                       VALUES (%s,%s,%s,'section_summary',%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        sid, file_id, tenant_id, "/".join(it.get("heading_path") or []),
                        it["summary_text"], it["summary_text"], simhash.simhash_hex(it["summary_text"]),
                        srcs, coverage_ratio, target,
                    ),
                )
                cur.execute(
                    """INSERT INTO kb_anchor(id,summary_doc_id,file_id,section_path,target_chunk_id,
                       target_content_hash,fingerprint,validity,anchor_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'valid',%s)""",
                    (
                        str(uuid.uuid4()), sid, file_id, "/".join(it.get("heading_path", []) or []) or None, tgt,
                        hashlib.sha256(tgt_content.encode()).hexdigest() if tgt_content else None,
                        simhash.simhash_hex(tgt_content) if tgt_content else None, target,
                    ),
                )
                summary_sources.append(_summary_source(it, it["_vec"], file_id, tenant_id, coverage_ratio))
            cur.execute(
                """INSERT INTO kb_version(id,file_id,doc_version,chunk_version,summary_version,anchor_version)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), file_id, target, target, target, target),
            )
            for cid, src in zip(chunk_ids, chunk_sources):
                cur.execute(
                    "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'index',%s)",
                    (file_id, json.dumps({"id": cid, "source": src})),
                )
            for sid, src in zip(summary_ids, summary_sources):
                cur.execute(
                    "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'index',%s)",
                    (file_id, json.dumps({"id": sid, "source": src})),
                )

    # drain staging → ES
    relay.drain(file_id)
    if relay.pending_count(file_id) > 0:  # drain barrier：未全发布则不 flip（无半可见）
        raise RuntimeError(f"outbox drain incomplete for {file_id} v{target}; staging left, not flipped")

    # PG 事务 B —— flip 原子发布（#22/#28）
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM kb_chunk WHERE file_id=%s AND chunk_version<%s", (file_id, target)
            )
            old_chunk_ids = [str(r[0]) for r in cur.fetchall()]
            cur.execute(
                "SELECT id FROM kb_summary_doc WHERE file_id=%s AND summary_version<%s", (file_id, target)
            )
            old_summary_ids = [str(r[0]) for r in cur.fetchall()]
            cur.execute(
                """UPDATE kb_file SET active_doc_version=%s, active_chunk_version=%s,
                   active_summary_version=%s, active_anchor_version=%s, status='ready', page_count=%s
                   WHERE id=%s""",
                (target, target, target, target, len(blocks), file_id),
            )
            cur.execute(
                "UPDATE kb_chunk SET available=1 WHERE file_id=%s AND chunk_version=%s", (file_id, target)
            )
            cur.execute(
                "UPDATE kb_chunk SET available=0 WHERE file_id=%s AND chunk_version<%s", (file_id, target)
            )
            new_ids = chunk_ids + summary_ids
            if new_ids:
                cur.execute(
                    "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'set_available',%s)",
                    (file_id, json.dumps({"ids": new_ids, "available": 1})),
                )
            old_ids = old_chunk_ids + old_summary_ids
            if old_ids:
                cur.execute(
                    "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'set_available',%s)",
                    (file_id, json.dumps({"ids": old_ids, "available": 0})),
                )

    # drain flip → ES available 翻转
    relay.drain(file_id)
    return {
        "file_id": file_id,
        "version": target,
        "chunks": len(new_chunks),
        "summaries": len(summary_items),
        "coverage": round(coverage_ratio, 3),
        **metrics,
    }

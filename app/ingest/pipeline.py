"""端到端摄取管线（T11：stage→drain→flip 版本化原子发布）。

parse → chunk → embed → 总结+锚点，全部以 target_version 暂存（available=0）+ outbox 同事务写入；
relay 把 outbox 发布到 ES；drain barrier 通过后，单 PG 事务 flip 四 active 指针 + available（原子发布）。
评审 #11（outbox）/#22/#28（active 指针+原子发布）/#25（四元组）/#12（重建 saga）。
"""
import hashlib
import json
import uuid

from app.adapters import embedder, parser
from app.config import settings
from app.db import get_conn
from app.ingest import chunker, summarizer
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


def ingest_file(file_id: str) -> dict:
    """stage（暂存+outbox）→ drain → flip（原子发布）。返回统计。"""
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

    # 0. 解析 + 分块 + embed
    blocks = parser.parse_bytes(_read_file_bytes(f["storage_key"]), f.get("mime"), f["name"] or "")
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
    vectors = embedder.embed_batch([c["content"] for c in chunks])
    chunk_sources = [_chunk_source(c, vectors[i], file_id, tenant_id, target) for i, c in enumerate(chunks)]

    # 1. 总结 + 锚点
    chunk_by_id = {c["chunk_id"]: c for c in chunks}
    summary_items = summarizer.summarize_file(chunks) if f.get("summary_enabled") else []
    covered = {cid for it in summary_items for cid in it["source_chunk_ids"]}
    coverage_ratio = (len(covered) / len(chunks)) if chunks else 0.0
    sum_vecs = embedder.embed_batch([it["summary_text"] for it in summary_items]) if summary_items else []
    summary_ids: list[str] = []
    summary_sources: list[dict] = []
    for i, it in enumerate(summary_items):
        summary_sources.append(_summary_source(it, sum_vecs[i], file_id, tenant_id, coverage_ratio))

    # 2. PG 事务 A —— 暂存（available=0）+ outbox 同事务（#11 原子）
    chunk_ids = [c["chunk_id"] for c in chunks]
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
                    for c in chunks
                ],
            )
            for i, it in enumerate(summary_items):
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
                        str(uuid.uuid4()), sid, file_id, "/".join(it.get("heading_path", [])) or None, tgt,
                        hashlib.sha256(tgt_content.encode()).hexdigest() if tgt_content else None,
                        simhash.simhash_hex(tgt_content) if tgt_content else None, target,
                    ),
                )
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

    # 3. drain staging → ES
    relay.drain(file_id)
    if relay.pending_count(file_id) > 0:  # drain barrier：未全发布则不 flip（无半可见）
        raise RuntimeError(f"outbox drain incomplete for {file_id} v{target}; staging left, not flipped")

    # 4. PG 事务 B —— flip 原子发布（#22/#28）
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

    # 5. drain flip → ES available 翻转
    relay.drain(file_id)
    return {
        "file_id": file_id,
        "version": target,
        "chunks": len(chunks),
        "summaries": len(summary_items),
        "coverage": round(coverage_ratio, 3),
    }

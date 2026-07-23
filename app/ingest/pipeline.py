"""端到端摄取管线（同步）：parse → chunk → embed → 索引(路B) → 总结+锚点(路A) → PG/ES 写入 → flip ready。"""
import hashlib
import uuid

from elasticsearch import helpers

from app.adapters import embedder, parser
from app.config import settings
from app.db import get_conn
from app.es import INDEX, get_es
from app.ingest import chunker, summarizer
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


def ingest_file(file_id: str) -> dict:
    """从 PG 读 file 元信息 + MinIO 读原文，跑完整摄取。返回统计。"""
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM kb_file WHERE id=%s", (file_id,))
            f = cur.fetchone()
    if not f:
        raise FileNotFoundError(file_id)

    # 0. 解析
    blocks = parser.parse_bytes(_read_file_bytes(f["storage_key"]), f.get("mime"), f["name"] or "")
    # 1. 分块
    raw = chunker.chunk_blocks(blocks)
    doc_version = 1
    chunks = []
    for c in raw:
        cid = _chunk_id(file_id, doc_version, c["chunk_order"])
        chunks.append({**c, "chunk_id": cid, "content_hash": hashlib.sha256(c["content"].encode()).hexdigest()})
    # 2. embedding
    vectors = embedder.embed_batch([c["content"] for c in chunks])
    # 3. 写 ES（路 B：doc_type=chunk）
    es = get_es()
    actions = [
        {
            "_index": INDEX,
            "_id": c["chunk_id"],
            "_source": {
                "content_tks": c["content"],
                "q_vec_vec": vectors[i],
                "file_id_kwd": file_id,
                "doc_type_kwd": "chunk",
                "page_num_int": c["page"],
                "chunk_order_int": c["chunk_order"],
                "chunk_version_int": doc_version,
                "available_int": 1,
            },
        }
        for i, c in enumerate(chunks)
    ]
    for a in actions:
        es.index(index=a["_index"], id=a["_id"], document=a["_source"])
    es.indices.refresh(index=INDEX)
    # 4. 写 PG kb_chunk
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO kb_chunk(id,file_id,doc_version,chunk_order,content,content_ltks,
                   section_path,page_num,position,chunk_version,content_hash,available)
                   VALUES (%(id)s,%(file_id)s,%(dv)s,%(co)s,%(ct)s,%(ct)s,%(sp)s,%(pg)s,null,1,%(ch)s,1)""",
                [
                    {
                        "id": c["chunk_id"], "file_id": file_id, "dv": doc_version,
                        "co": c["chunk_order"], "ct": c["content"], "sp": c["section_path"],
                        "pg": c["page"], "ch": c["content_hash"],
                    }
                    for c in chunks
                ],
            )
    # 5. 总结 + 锚点（路 A）
    summary_items = summarizer.summarize_file(chunks) if f.get("summary_enabled") else []
    covered = {cid for it in summary_items for cid in it["source_chunk_ids"]}
    coverage_ratio = (len(covered) / len(chunks)) if chunks else 0.0
    if summary_items:
        sum_texts = [it["summary_text"] for it in summary_items]
        sum_vecs = embedder.embed_batch(sum_texts)
        sum_actions = []
        with get_conn() as conn:
            with conn.cursor() as cur:
                for i, it in enumerate(summary_items):
                    sid = str(uuid.uuid4())
                    srcs = [str(s) for s in it["source_chunk_ids"]]
                    cur.execute(
                        """INSERT INTO kb_summary_doc(id,file_id,summary_type,heading_path,content_md,
                           summary_text,source_chunk_ids,coverage_ratio,summary_version)
                           VALUES (%s,%s,'section_summary',%s,%s,%s,%s,%s,1)""",
                        (sid, file_id, "/".join(it.get("heading_path") or []), it["summary_text"], it["summary_text"], srcs, coverage_ratio),
                    )
                    # 锚点指向首个 source chunk（MVP 简版）
                    tgt = srcs[0] if srcs else None
                    fp = summarizer.fingerprint(it["summary_text"])
                    cur.execute(
                        """INSERT INTO kb_anchor(id,summary_doc_id,file_id,section_path,target_chunk_id,fingerprint,validity,anchor_version)
                           VALUES (%s,%s,%s,%s,%s,%s,'valid',1)""",
                        (str(uuid.uuid4()), sid, file_id, "/".join(it.get("heading_path", [])) or None, tgt, fp),
                    )
                    sum_actions.append(
                        {
                            "_index": INDEX,
                            "_id": sid,
                            "_source": {
                                "content_tks": it["summary_text"],
                                "q_vec_vec": sum_vecs[i],
                                "file_id_kwd": file_id,
                                "doc_type_kwd": "summary",
                                "is_summary_int": 1,
                                "source_chunk_ids_kwd": srcs,
                                "available_int": 1,
                            },
                        }
                    )
        for a in sum_actions:
            es.index(index=a["_index"], id=a["_id"], document=a["_source"])
        es.indices.refresh(index=INDEX)

    # 6. flip ready
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE kb_file SET status='ready', page_count=%s WHERE id=%s",
                (len(blocks), file_id),
            )
    return {"file_id": file_id, "chunks": len(chunks), "summaries": len(summary_items), "coverage": round(coverage_ratio, 3)}

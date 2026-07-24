"""增量更新 diff/复用（T12，评审 #26）。

按 content_hash 精确匹配新老版本 chunk；未变 chunk 复用旧 embedding（读旧 ES doc 向量，
pipeline 以新 chunk_id/新 version 重发，不调 embedder）；source_chunk_ids 全命中的旧 summary
复用（pipeline remap 到新 chunk_id + 复用 summary 向量，不调 LLM）。

simhash 模糊匹配 / L2 reduce / 局部复用 留精修。diff 只负责匹配 + 向量检索；
ES source 构造复用 pipeline._chunk_source/_summary_source（向量来自此处）。
"""
from app.db import get_conn
from app.es import INDEX, get_es


def prev_chunks(file_id: str, version: int) -> list[dict]:
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, content_hash FROM kb_chunk WHERE file_id=%s AND chunk_version=%s",
                (file_id, version),
            )
            return [{"id": str(r["id"]), "content_hash": r["content_hash"]} for r in cur.fetchall()]


def match(new_chunks: list[dict], prev: list[dict]) -> tuple[dict, list[dict]]:
    """返回 (matched: content_hash->prev_row, fresh: 未命中的 new_chunks)。"""
    prev_by_hash = {p["content_hash"]: p for p in prev}
    matched: dict[str, dict] = {}
    fresh: list[dict] = []
    for c in new_chunks:
        p = prev_by_hash.get(c["content_hash"])
        if p:
            matched[c["content_hash"]] = p
        else:
            fresh.append(c)
    return matched, fresh


def old_to_new_map(matched: dict, new_chunks: list[dict]) -> dict[str, str]:
    """prev_chunk_id -> new_chunk_id（用于 summary source_chunk_ids remap）。"""
    m: dict[str, str] = {}
    for c in new_chunks:
        p = matched.get(c["content_hash"])
        if p:
            m[p["id"]] = c["chunk_id"]
    return m


def prev_summaries(file_id: str, version: int) -> list[dict]:
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, summary_text, source_chunk_ids, heading_path FROM kb_summary_doc WHERE file_id=%s AND summary_version=%s",
                (file_id, version),
            )
            return [
                {
                    "id": str(r["id"]),
                    "summary_text": r["summary_text"],
                    "source_chunk_ids": [str(s) for s in r["source_chunk_ids"]],
                    "heading_path": (r["heading_path"].split("/") if r["heading_path"] else None),
                }
                for r in cur.fetchall()
            ]


def reuse_vector(doc_id: str):
    """读旧 ES doc 的 q_vec_vec（chunk 或 summary），复用时由 pipeline 套进新 source。"""
    try:
        return get_es().get(index=INDEX, id=doc_id, source_includes=["q_vec_vec"])["_source"].get("q_vec_vec")
    except Exception:
        return None

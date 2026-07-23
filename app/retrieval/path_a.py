"""路 A（简版）：检索总结文档 → 取 source_chunk_ids 锚点 → 选首个 → 回原文窗口 →
逐 chunk 软校验（红队 B1：规避 BGE 上限 + 保留判别力）剔 gross miss。

中期补：simhash 稳定锚重定位、置信度+section 锚点选择、邻域扩展、软截止只返已校验。"""
import math

from app.config import settings
from app.db import get_conn
from app.es import INDEX, get_es

THETA_A = settings.path_a_theta  # gross-miss 软门控（生产真 embedding 用 0.2）


def _es_chunk_vec(chunk_id: str) -> list[float] | None:
    r = get_es().get(index=INDEX, id=chunk_id, source_includes=["q_vec_vec"])
    return r["_source"].get("q_vec_vec")


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def read_window(file_id: str, chunk_id: str, before: int = 2, after: int = 4) -> list[dict]:
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT chunk_order FROM kb_chunk WHERE id=%s AND available=1", (chunk_id,))
            r = cur.fetchone()
            if not r:
                return []
            order = r["chunk_order"]
            cur.execute(
                """SELECT * FROM kb_chunk WHERE file_id=%s AND available=1
                   AND chunk_order BETWEEN %s AND %s ORDER BY chunk_order""",
                (file_id, order - before, order + after),
            )
            return cur.fetchall()


def search(q_vec: list[float], query_text: str, file_ids: list[str], top_k: int = 5) -> list[dict]:
    if not file_ids:
        return []
    filt = [
        {"term": {"doc_type_kwd": "summary"}},
        {"term": {"available_int": 1}},
        {"terms": {"file_id_kwd": file_ids}},
    ]
    body = {
        "size": top_k,
        "query": {"bool": {"filter": filt, "should": [{"match": {"content_tks": query_text}}]}},
        "knn": {
            "field": "q_vec_vec",
            "query_vector": q_vec,
            "k": top_k,
            "num_candidates": max(top_k * 4, 50),
            "filter": filt,
        },
    }
    res = get_es().search(index=INDEX, body=body)
    out: list[dict] = []
    for h in res["hits"]["hits"]:
        src_ids = h["_source"].get("source_chunk_ids_kwd", [])
        file_id = h["_source"].get("file_id_kwd")
        for cid in src_ids[:1]:  # MVP：锚点选首个（中期改置信度+section 命中度）
            vec = _es_chunk_vec(cid)
            if not vec or _cos(q_vec, vec) < THETA_A:
                continue  # 软校验剔 gross miss
            rows = read_window(file_id, cid)
            if not rows:
                continue
            out.append(
                {
                    "chunk_id": cid,
                    "file_id": file_id,
                    "page": rows[0]["page_num"],
                    "content": "\n".join(r["content"] for r in rows),
                    "score": h["_score"],
                    "path": "a",
                }
            )
    return out

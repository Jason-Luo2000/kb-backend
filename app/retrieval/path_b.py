"""路 B：BM25 + KNN 混合召回基线（方案 §4.3）。ES 顶层 knn + query 分数自动合并。"""
from app.es import INDEX, get_es


def search(q_vec: list[float], query_text: str, file_ids: list[str], top_n: int = 24) -> list[dict]:
    if not file_ids:
        return []
    filt = [
        {"term": {"doc_type_kwd": "chunk"}},
        {"term": {"available_int": 1}},
        {"terms": {"file_id_kwd": file_ids}},
    ]
    body = {
        "size": top_n,
        "query": {"bool": {"filter": filt, "should": [{"match": {"content_tks": query_text}}]}},
        "knn": {
            "field": "q_vec_vec",
            "query_vector": q_vec,
            "k": top_n,
            "num_candidates": max(top_n * 4, 50),
            "filter": filt,
        },
    }
    res = get_es().search(index=INDEX, body=body)
    out = []
    for h in res["hits"]["hits"]:
        s = h["_source"]
        out.append(
            {
                "chunk_id": h["_id"],
                "file_id": s.get("file_id_kwd"),
                "page": s.get("page_num_int"),
                "content": s.get("content_tks", ""),
                "score": h["_score"],
                "path": "b",
            }
        )
    return out

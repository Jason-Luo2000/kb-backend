"""路 A（T10 完整版）：总结导航 → AnchorResolver 稳定锚 → 原文窗口 → 逐 chunk 软门控 → 超时软截止。

相对 MVP 简版的四项修正（评审 #1/#4/#8/#9/#20）：
- 锚点用 simhash 稳定锚（AnchorResolver：valid/relocated/stale），不再裸 chunk_id；
- 锚点选择按 query↔chunk 文本重叠（#9），非 sim(q_vec,chunk_vec)；
- 软门控：原文窗口内逐 chunk 取 max cos（#1，规避大窗口 mean-pool 信号平滑），θ_a 仅剔 gross miss；
  θ_a<0 跳过门控（兼容哈希伪向量模式）；
- 超时软截止（#8）：逐命中前检查 deadline，返回已校验部分命中 + 退化原因。
"""
import math
import time

from app.config import settings
from app.db import get_conn
from app.es import INDEX, get_es
from app.retrieval import anchor

THETA_A = settings.path_a_theta  # gross-miss 软门控（生产真 embedding 用 0.2；哈希伪向量设 -1 跳过）


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _es_vecs(chunk_ids: list[str]) -> list[list[float] | None]:
    """批量取窗口块已存向量（FakeES 无 mget，逐 get；生产可换 mget）。"""
    es = get_es()
    out = []
    for cid in chunk_ids:
        try:
            r = es.get(index=INDEX, id=cid, source_includes=["q_vec_vec"])
            out.append(r["_source"].get("q_vec_vec"))
        except Exception:
            out.append(None)
    return out


def read_window(file_id: str, chunk_id: str, before: int = 2, after: int = 4, tenant_id: str | None = None) -> list[dict]:
    """取锚点 chunk 及 before/after 邻域的真实原文窗口。"""
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT chunk_order FROM kb_chunk WHERE id=%s AND available=1"
                + (" AND tenant_id=%s" if tenant_id else ""),
                (chunk_id,) + ((tenant_id,) if tenant_id else ()),
            )
            r = cur.fetchone()
            if not r:
                return []
            order = r["chunk_order"]
            cur.execute(
                """SELECT * FROM kb_chunk WHERE file_id=%s AND available=1
                   AND chunk_order BETWEEN %s AND %s"""
                + (" AND tenant_id=%s" if tenant_id else "")
                + """ ORDER BY chunk_order""",
                (file_id, order - before, order + after) + ((tenant_id,) if tenant_id else ()),
            )
            return cur.fetchall()


def _read_and_gate(q_vec, file_id, chunk_id, before, after, tenant_id) -> dict | None:
    """读窗口 + 软门控；gross miss 返回 None（调用方扩邻域重试）。"""
    rows = read_window(file_id, chunk_id, before, after, tenant_id)
    if not rows:
        return None
    if THETA_A >= 0:  # 真向量模式才门控（哈希伪向量 θ_a<0 跳过）
        vecs = _es_vecs([r["id"] for r in rows])
        best = max((_cos(q_vec, v) for v in vecs if v), default=-1.0)
        if best < THETA_A:
            return None
    return {
        "chunk_id": chunk_id,
        "file_id": file_id,
        "page": rows[0]["page_num"],
        "content": "\n".join(r["content"] for r in rows),
        "score": 0.0,  # RRF 会按 rank 重算
    }


def _degraded_reason(reasons: list[str], out_count: int, total: int) -> str:
    if total == 0:
        return "no_summary"
    if out_count and not reasons:
        return "none"
    uniq = sorted(set(reasons))
    if out_count:
        return "partial:" + ",".join(uniq)
    return uniq[0] if uniq else "empty"


def search(
    q_vec: list[float],
    query_text: str,
    file_ids: list[str],
    tenant_id: str,
    clearance: int = 4,
    top_k: int = 5,
    before: int = 2,
    after: int = 4,
) -> dict:
    """返回 {hits, degraded, completed, total}。"""
    deadline = time.time() + settings.path_a_timeout_ms / 1000
    if not file_ids:
        return {"hits": [], "degraded": "no_files", "completed": 0, "total": 0}
    filt = [
        {"term": {"doc_type_kwd": "summary"}},
        {"term": {"available_int": 1}},
        {"term": {"tenant_id_kwd": tenant_id}},
        {"range": {"sensitivity_int": {"lte": clearance}}},
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
    reasons: list[str] = []
    total = len(res["hits"]["hits"])
    for h in res["hits"]["hits"]:  # ES 分排序 = 处理优先级
        if time.time() >= deadline:
            reasons.append("timeout")
            break
        src = h["_source"]
        file_id = src.get("file_id_kwd")
        src_ids = src.get("source_chunk_ids_kwd", [])
        r = anchor.resolve(file_id, src_ids, h["_id"], query_text, tenant_id)
        if r.validity == "stale" or not r.chunk_id:
            reasons.append("anchor_stale")
            continue
        hit = _read_and_gate(q_vec, file_id, r.chunk_id, before, after, tenant_id)
        if hit is None:  # 软门控不达标 → 扩邻域重试一次
            hit = _read_and_gate(q_vec, file_id, r.chunk_id, before * 2, after * 2, tenant_id)
        if hit is None:
            reasons.append("relevance_fail")
            continue
        hit["path"] = "a"
        hit["weight"] = float(src.get("coverage_ratio_f") or 1.0)  # 低 coverage → 融合降权
        out.append(hit)
    return {
        "hits": out,
        "degraded": _degraded_reason(reasons, len(out), total),
        "completed": len(out),
        "total": total,
    }

"""双路编排：并行路 A/路 B → RRF 融合 → read-anchor，落 query_log。
T9：所有 file_id 解析收敛到 principal 的租户与授权 kb；post-verify 兜底越权。"""
import time
from concurrent.futures import ThreadPoolExecutor

from app.adapters import embedder
from app.authz import resolve as resolve_authz
from app.config import settings
from app.db import get_conn
from app.middleware.auth import Principal
from app.metrics import PATH_A_RATE
from app.retrieval import fusion, path_a, path_b


def _allowed_file_ids(kb_ids: list[str] | None, principal: Principal) -> tuple[list[str], int]:
    """返回 (允许读的 file_id 列表, clearance)。
    kb 请求集 ∩ AuthzDecision.allowed_kb_ids，且 JOIN kb_kb 强制租户隔离（纵深）。"""
    decision = resolve_authz(principal)
    allowed = decision.allowed_kb_ids
    wanted = [k for k in (kb_ids or []) if k in allowed] if kb_ids else list(allowed)
    if not wanted:
        return [], decision.clearance
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT fk.file_id FROM kb_file_kb fk
                   JOIN kb_kb k ON k.id = fk.kb_id
                   WHERE k.tenant_id = %s AND fk.kb_id = ANY(%s)""",
                (principal.tenant_id, wanted),
            )
            file_ids = [str(r[0]) for r in cur.fetchall()]
    return file_ids, decision.clearance


def _retrieve_core(
    query: str,
    principal: Principal,
    kb_ids: list[str] | None = None,
    top_k: int | None = None,
    mode: str = "hybrid",
    similarity_threshold: float | None = None,
    rerank: bool | None = None,
) -> tuple[list[dict], dict]:
    """检索内核（双路→RRF→rerank→postverify→threshold→top_k）。返回 (merged_internal, meta)。
    merged 含**全文** content（供生成；早于 _hit_view 截断）。rerank 三态：None/True=自动(配了就走)、False=关。
    meta 字段统一：path_a/path_b/degraded/rerank_used/latency_ms/pa_rate/pa_degraded_reason/empty。"""
    from app.adapters import rerank as rerank_mod
    from app.retrieval import guard

    t0 = time.time()
    top_k = top_k or settings.default_top_k
    file_ids, clearance = _allowed_file_ids(kb_ids, principal)
    if not file_ids:
        return [], _meta(0, 0, "both_empty", False, 0, None, "no_files", True)

    q_vec = embedder.embed(query)
    a_meta = {"hits": [], "degraded": "no_path", "completed": 0, "total": 0}
    with ThreadPoolExecutor(max_workers=2) as ex:
        tasks = {}
        if mode in ("hybrid", "summary"):
            tasks["a"] = ex.submit(path_a.search, q_vec, query, file_ids, principal.tenant_id, clearance)
        if mode in ("hybrid", "embedding"):
            tasks["b"] = ex.submit(path_b.search, q_vec, query, file_ids, principal.tenant_id, clearance)
        if "a" in tasks:
            a_meta = tasks["a"].result()
        b = tasks["b"].result() if "b" in tasks else []
    a = a_meta["hits"]

    merged = fusion.rrf_merge(a, b)
    # rerank（None/True→配了就走；False→关）
    used_rerank = False
    if rerank is not False:
        rr = rerank_mod.rerank(query, [h["content"] for h in merged], principal.tenant_id)
        if rr is not None and merged:
            used_rerank = True
            merged = [merged[idx] for idx, _ in rr if idx < len(merged)]
    # 相似度阈值：按融合 score 过滤低质命中（None/0=不过滤）
    if similarity_threshold:
        merged = [h for h in merged if float(h.get("score", 0) or 0) >= similarity_threshold]
    # post-verify 越权兜底 + top_k 切片
    if used_rerank:
        merged = guard.postverify(merged, principal, file_ids)[:top_k]
    else:
        merged = guard.postverify(merged[:top_k], principal, file_ids)

    degraded = "none" if (a and b) else ("b_only" if b else ("a_only" if a else "both_empty"))
    pa_rate = round(a_meta["completed"] / a_meta["total"], 3) if a_meta["total"] else None
    if pa_rate is not None:  # T16：查询侧 SLO 直方图
        PATH_A_RATE.observe(pa_rate)
    latency_ms = int((time.time() - t0) * 1000)
    _log_query(principal, query, file_ids, len(a), len(b), degraded, latency_ms, used_rerank)
    return merged, _meta(len(a), len(b), degraded, used_rerank, latency_ms, pa_rate, a_meta["degraded"], False)


def _meta(path_a_n, path_b_n, degraded, rerank_used, latency_ms, pa_rate, pa_reason, empty):
    return {
        "path_a": path_a_n,
        "path_b": path_b_n,
        "degraded": degraded,
        "rerank_used": rerank_used,
        "latency_ms": latency_ms,
        "path_a_completed_rate": pa_rate,
        "path_a_degraded_reason": pa_reason,
        "empty": empty,
    }


def retrieve(
    query: str,
    principal: Principal,
    kb_ids: list[str] | None = None,
    top_k: int | None = None,
    mode: str = "hybrid",
) -> dict:
    """纯检索（/v1/search）：{hits, route_stats}。行为不变。"""
    merged, meta = _retrieve_core(query, principal, kb_ids, top_k, mode)
    return {"hits": [_hit_view(h) for h in merged], "route_stats": {k: meta[k] for k in (
        "path_a", "path_b", "degraded", "rerank_used", "latency_ms",
        "path_a_completed_rate", "path_a_degraded_reason")}}


def chat_retrieve(
    query: str,
    principal: Principal,
    kb_ids: list[str] | None = None,
    top_k: int | None = None,
    mode: str = "hybrid",
    similarity_threshold: float | None = None,
    rerank: bool | None = None,
) -> tuple[list[dict], dict]:
    """RAG 检索（/v1/chat 用）：返回 (merged_全文, meta) 供 generate 合成答案。"""
    return _retrieve_core(query, principal, kb_ids, top_k, mode, similarity_threshold, rerank)


def _hit_view(h: dict) -> dict:
    return {
        "docId": h["file_id"],
        "chunkId": h["chunk_id"],
        "page": h["page"],
        "snippet": h["content"][:600],
        "score": round(float(h["score"]), 4),
        "path": h["path"],
        "citation": {"chunkId": h["chunk_id"], "page": h["page"]},
    }


def read_anchor(file_id: str, anchor: str, principal: Principal, before: int = 2, after: int = 4) -> dict | None:
    """精读原文窗口。调用方须先确认 file_id ∈ allowed（见 docs.read_anchor 的 ACL 闸门）。"""
    rows = path_a.read_window(file_id, anchor, before, after, principal.tenant_id)
    if not rows:
        return None
    return {
        "docId": file_id,
        "anchor": anchor,
        "text": "\n".join(r["content"] for r in rows),
        "page": rows[0]["page_num"],
        "version": 1,
    }


def _log_query(principal: Principal, q: str, fids: list[str], a: int, b: int, deg: str, lat: int,
               rerank_used: bool = False) -> None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_query_log(tenant_id,user_id,query_norm,file_ids,path_a_hits,path_b_hits,
                       path_degraded,latency_ms,rerank_used)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (principal.tenant_id, principal.user_id, q, fids, a, b, deg, lat, rerank_used),
                )
    except Exception:
        pass

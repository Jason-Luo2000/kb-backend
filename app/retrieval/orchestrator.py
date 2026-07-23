"""双路编排：并行路 A/路 B → RRF 融合 → read-anchor，落 query_log。
T9：所有 file_id 解析收敛到 principal 的租户与授权 kb；post-verify 兜底越权。"""
import time
from concurrent.futures import ThreadPoolExecutor

from app.adapters import embedder
from app.authz import resolve as resolve_authz
from app.config import settings
from app.db import get_conn
from app.middleware.auth import Principal
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


def retrieve(
    query: str,
    principal: Principal,
    kb_ids: list[str] | None = None,
    top_k: int | None = None,
    mode: str = "hybrid",
) -> dict:
    t0 = time.time()
    top_k = top_k or settings.default_top_k
    file_ids, clearance = _allowed_file_ids(kb_ids, principal)
    if not file_ids:
        _log_query(principal, query, [], 0, 0, "both_empty", 0)
        return {"hits": [], "route_stats": {"path_a": 0, "path_b": 0, "degraded": "both_empty", "latency_ms": 0}}

    q_vec = embedder.embed(query)
    a: list[dict] = []
    b: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        tasks = {}
        if mode in ("hybrid", "summary"):
            tasks["a"] = ex.submit(path_a.search, q_vec, query, file_ids, principal.tenant_id, clearance)
        if mode in ("hybrid", "embedding"):
            tasks["b"] = ex.submit(path_b.search, q_vec, query, file_ids, principal.tenant_id, clearance)
        a = tasks["a"].result() if "a" in tasks else []
        b = tasks["b"].result() if "b" in tasks else []

    merged = fusion.rrf_merge(a, b)[:top_k]
    # post-verify：逐 chunk 回查租户，丢弃越权命中（Phase 6 guard）
    from app.retrieval import guard

    merged = guard.postverify(merged, principal, file_ids)
    degraded = "none" if (a and b) else ("b_only" if b else ("a_only" if a else "both_empty"))
    latency_ms = int((time.time() - t0) * 1000)
    _log_query(principal, query, file_ids, len(a), len(b), degraded, latency_ms)
    return {
        "hits": [_hit_view(h) for h in merged],
        "route_stats": {"path_a": len(a), "path_b": len(b), "degraded": degraded, "latency_ms": latency_ms},
    }


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


def _log_query(principal: Principal, q: str, fids: list[str], a: int, b: int, deg: str, lat: int) -> None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_query_log(tenant_id,user_id,query_norm,file_ids,path_a_hits,path_b_hits,
                       path_degraded,latency_ms)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (principal.tenant_id, principal.user_id, q, fids, a, b, deg, lat),
                )
    except Exception:
        pass

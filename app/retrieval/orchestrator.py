"""双路编排：并行路 A/路 B → RRF 融合 → read-anchor，落 query_log。"""
import time
from concurrent.futures import ThreadPoolExecutor

from app.adapters import embedder
from app.config import settings
from app.db import get_conn
from app.retrieval import fusion, path_a, path_b


def _allowed_file_ids(kb_ids: list[str] | None) -> list[str]:
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if kb_ids:
                cur.execute("SELECT file_id FROM kb_file_kb WHERE kb_id = ANY(%s)", (kb_ids,))
            else:
                cur.execute("SELECT file_id FROM kb_file_kb")
            return [str(r["file_id"]) for r in cur.fetchall()]


def retrieve(query: str, kb_ids: list[str] | None = None, top_k: int | None = None, mode: str = "hybrid") -> dict:
    t0 = time.time()
    top_k = top_k or settings.default_top_k
    file_ids = _allowed_file_ids(kb_ids)
    if not file_ids:
        return {"hits": [], "route_stats": {"path_a": 0, "path_b": 0, "degraded": "both_empty", "latency_ms": 0}}

    q_vec = embedder.embed(query)
    a: list[dict] = []
    b: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        tasks = {}
        if mode in ("hybrid", "summary"):
            tasks["a"] = ex.submit(path_a.search, q_vec, query, file_ids)
        if mode in ("hybrid", "embedding"):
            tasks["b"] = ex.submit(path_b.search, q_vec, query, file_ids)
        a = tasks["a"].result() if "a" in tasks else []
        b = tasks["b"].result() if "b" in tasks else []

    merged = fusion.rrf_merge(a, b)[:top_k]
    degraded = "none" if (a and b) else ("b_only" if b else ("a_only" if a else "both_empty"))
    latency_ms = int((time.time() - t0) * 1000)
    _log_query(query, file_ids, len(a), len(b), degraded, latency_ms)
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


def read_anchor(file_id: str, anchor: str, before: int = 2, after: int = 4) -> dict | None:
    rows = path_a.read_window(file_id, anchor, before, after)
    if not rows:
        return None
    return {
        "docId": file_id,
        "anchor": anchor,
        "text": "\n".join(r["content"] for r in rows),
        "page": rows[0]["page_num"],
        "version": 1,
    }


def _log_query(q: str, fids: list[str], a: int, b: int, deg: str, lat: int) -> None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_query_log(query_norm,file_ids,path_a_hits,path_b_hits,path_degraded,latency_ms)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (q, fids, a, b, deg, lat),
                )
    except Exception:
        pass

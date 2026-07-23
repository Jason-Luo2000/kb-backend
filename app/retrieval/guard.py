"""RetrievalGuard.postverify：RRF merge 后逐 chunk 回查租户，丢弃越权命中 + SEC_VIOLATION 审计。

纵深防御最后一道：file_id allowlist（应用层）+ tenant_id_kwd（ES filter）已是主防线，
此为兜底——防 ES 返回脏数据 / 版本漂移 / 未来检索路径绕过。方案 §4.4「预过滤 + post-verify」。
"""
from app.db import get_conn
from app.middleware.auth import Principal, audit


def postverify(hits: list[dict], principal: Principal, allowed_file_ids: list[str]) -> list[dict]:
    if not hits:
        return hits
    allowed = set(allowed_file_ids)
    chunk_ids = [h["chunk_id"] for h in hits]
    tenants: dict[str, str | None] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, tenant_id FROM kb_chunk WHERE id = ANY(%s)", (chunk_ids,))
            for cid, tid in cur.fetchall():
                tenants[str(cid)] = str(tid) if tid else None
    kept: list[dict] = []
    dropped: list[str] = []
    for h in hits:
        cid = h["chunk_id"]
        if tenants.get(cid) == principal.tenant_id and h.get("file_id") in allowed:
            kept.append(h)
        else:
            dropped.append(cid)
    if dropped:
        audit("SEC_VIOLATION", principal=principal, hits=dropped, result="dropped")
    return kept

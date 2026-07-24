"""RetrievalGuard.postverify：RRF merge 后逐 chunk 回查，丢弃越权/越版本命中 + SEC_VIOLATION 审计。

纵深防御：
- 租户：chunk.tenant_id == principal.tenant_id 且 file_id ∈ allowed（T9）；
- 版本栅栏（#28）：chunk.chunk_version == kb_file.active_chunk_version（防 relay 漂移 / 暂存版本泄漏）。
"""
from app.db import get_conn
from app.middleware.auth import Principal, audit


def postverify(hits: list[dict], principal: Principal, allowed_file_ids: list[str]) -> list[dict]:
    if not hits:
        return hits
    allowed = set(allowed_file_ids)
    chunk_ids = [h["chunk_id"] for h in hits]
    file_ids = list({h["file_id"] for h in hits if h.get("file_id")})
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, tenant_id, chunk_version FROM kb_chunk WHERE id = ANY(%s)",
                (chunk_ids,),
            )
            rows = {str(r[0]): (str(r[1]) if r[1] else None, r[2]) for r in cur.fetchall()}
            active = {}
            if file_ids:
                cur.execute(
                    "SELECT id, active_chunk_version FROM kb_file WHERE id = ANY(%s)",
                    (file_ids,),
                )
                active = {str(r[0]): r[1] for r in cur.fetchall()}
    kept: list[dict] = []
    dropped: list[str] = []
    for h in hits:
        cid = h["chunk_id"]
        fid = h.get("file_id")
        tid, cv = rows.get(cid, (None, None))
        ok = tid == principal.tenant_id and fid in allowed and cv == active.get(fid)
        if ok:
            kept.append(h)
        else:
            dropped.append(cid)
    if dropped:
        audit("SEC_VIOLATION", principal=principal, hits=dropped, result="dropped")
    return kept

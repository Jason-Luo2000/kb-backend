"""检索：双路召回 / 引用。T9：透传 principal，cite chunk 回查收敛到租户+授权 kb。"""
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from app.authz import resolve as resolve_authz
from app.db import get_conn
from app.middleware.auth import audit, get_principal, limiter, verify_api_key
from app.retrieval import citation, orchestrator

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


@router.post("/search")
@limiter.limit("120/minute")
def search(request: Request, body: dict):
    principal = get_principal(request)
    res = orchestrator.retrieve(
        body["query"],
        principal,
        body.get("knowledgeBaseIds"),
        body.get("topK"),
        body.get("mode", "hybrid"),
    )
    audit(
        "SEARCH",
        request,
        query=body["query"],
        hits=[h["chunkId"] for h in res["hits"]],
        ua=request.headers.get("user-agent"),
    )
    return res


@router.post("/cite")
def cite(body: dict, request: Request):
    """pi 答案后回传 → 后端补全 chunk 的 doc/page（中期接句级 insert_citations）。
    chunk 回查强制 tenant_id + 授权 kb，防跨租户引用。"""
    principal = get_principal(request)
    cids = body.get("chunkIds", [])
    hits = []
    if cids:
        decision = resolve_authz(principal)
        with get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT c.id, c.file_id, c.page_num, c.content
                       FROM kb_chunk c
                       WHERE c.id = ANY(%s) AND c.tenant_id = %s
                         AND c.file_id IN (SELECT fk.file_id FROM kb_file_kb fk
                                           JOIN kb_kb k ON k.id = fk.kb_id
                                           WHERE k.tenant_id = %s AND fk.kb_id = ANY(%s))""",
                    (
                        cids,
                        principal.tenant_id,
                        principal.tenant_id,
                        decision.allowed_kb_ids or ["00000000-0000-0000-0000-000000000000"],
                    ),
                )
                for r in cur.fetchall():
                    hits.append(
                        {
                            "file_id": str(r["file_id"]),
                            "chunk_id": str(r["id"]),
                            "page": r["page_num"],
                            "content": r["content"],
                        }
                    )
    return citation.build_citation(body["answer"], hits)

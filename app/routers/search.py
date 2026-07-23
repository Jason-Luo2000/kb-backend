"""检索：双路召回 / 引用。"""
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from app.db import get_conn
from app.middleware.auth import audit, limiter, verify_api_key
from app.retrieval import citation, orchestrator

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


@router.post("/search")
@limiter.limit("120/minute")
def search(request: Request, body: dict):
    res = orchestrator.retrieve(
        body["query"], body.get("knowledgeBaseIds"), body.get("topK"), body.get("mode", "hybrid")
    )
    audit("SEARCH", query=body["query"], hits=[h["chunkId"] for h in res["hits"]], ua=request.headers.get("user-agent"))
    return res


@router.post("/cite")
def cite(body: dict):
    """pi 答案后回传 → 后端补全 chunk 的 doc/page（中期接句级 insert_citations）。"""
    cids = body.get("chunkIds", [])
    hits = []
    if cids:
        with get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT id,file_id,page_num,content FROM kb_chunk WHERE id=ANY(%s)", (cids,))
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

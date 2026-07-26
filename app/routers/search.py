"""检索：双路召回 / 引用 / RAG 问答。T9：透传 principal，cite chunk 回查收敛到租户+授权 kb。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row

from app.authz import resolve as resolve_authz
from app.db import get_conn
from app.middleware.auth import audit, get_principal, limiter, verify_api_key
from app.retrieval import citation, generate, orchestrator

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


def _log_chat(tenant_id, user_id, query, answer, model, outcome, hits, latency_ms):
    """chat 结果埋点（best-effort，不阻塞请求）。outcome=answered|no_result|error；answer 供管理员查问答记录。"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_chat_log(tenant_id,user_id,query,answer,model,outcome,hits,latency_ms)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, user_id, query, answer, model, outcome, hits, latency_ms),
                )
    except Exception:  # noqa: BLE001
        pass


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


@router.get("/chat/models")  # 问答页模型下拉用（非 owner，脱敏：仅 id/name/modelName/isDefault）
def chat_models(request: Request):
    from app.models_registry import list_models

    principal = get_principal(request)
    rows = list_models(principal.tenant_id, include_system=True)
    return [
        {"id": r["id"], "name": r["name"], "modelName": r["modelName"], "isDefault": r["isDefault"]}
        for r in rows
        if r["kind"] == "llm"
    ]


@router.post("/chat")
@limiter.limit("60/minute")
def chat(request: Request, body: dict):
    """RAG 问答：检索（双路+RRF+可选 rerank+threshold）→ LLM 生成带 [n] 引用的答案。
    body: {query, knowledgeBaseIds?, modelId?, systemPrompt?, temperature?, maxTokens?,
           topK?, similarityThreshold?, rerank?, mode="hybrid", cite=true}。
    返 {answer, references[], hits[], route_stats, model, error}。LLM 不可用→answer=null 降级。"""
    principal = get_principal(request)
    query = body["query"]
    merged, meta = orchestrator.chat_retrieve(
        query, principal,
        kb_ids=body.get("knowledgeBaseIds"),
        top_k=body.get("topK"),
        mode=body.get("mode", "hybrid"),
        similarity_threshold=body.get("similarityThreshold"),
        rerank=body.get("rerank"),
    )
    try:
        gen = generate.generate_answer(
            query, merged,
            model_id=body.get("modelId"),
            temperature=body.get("temperature"),
            max_tokens=body.get("maxTokens"),
            system_prompt=body.get("systemPrompt"),
            tenant_id=principal.tenant_id,
            cite=bool(body.get("cite", True)),
            history=body.get("history"),
        )
    except RuntimeError as e:
        if str(e) == "KB_MODEL_NOT_FOUND":
            raise HTTPException(status_code=400, detail="KB_MODEL_NOT_FOUND") from e
        raise
    audit("CHAT", request, query=query, hits=[h["chunk_id"] for h in merged], ua=request.headers.get("user-agent"))
    _log_chat(principal.tenant_id, principal.user_id, query, gen["answer"], gen["model"],
              "error" if gen["error"] else ("no_result" if not merged else "answered"),
              len(merged), meta["latency_ms"])
    return {
        "answer": gen["answer"],
        "references": gen["references"],
        "hits": [orchestrator._hit_view(h) for h in merged],
        "route_stats": {k: meta[k] for k in (
            "path_a", "path_b", "degraded", "rerank_used", "latency_ms",
            "path_a_completed_rate", "path_a_degraded_reason")},
        "model": gen["model"],
        "error": gen["error"],
    }


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

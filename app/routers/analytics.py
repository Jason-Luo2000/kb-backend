"""数据看板统计（analytics）：用量 / 高频问题 / 模型调用 / 回答质量。
owner/admin（is_tenant_admin）专属。?days=7 时间窗（0=全部）。聚合 kb_query_log / kb_chat_log / kb_ingest_cost_log。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.authz import is_tenant_admin
from app.db import get_conn
from app.middleware.auth import get_principal, verify_api_key

router = APIRouter(prefix="/v1/admin/analytics", dependencies=[Depends(verify_api_key)])


def _require_admin(request: Request):
    p = get_principal(request)
    if not is_tenant_admin(p):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_ADMIN")
    return p


def _where(days: int):
    """时间窗片段：返 (clause, args)。days>0 → 'AND created_at>=%s' + [since]；0=全部。"""
    if days and days > 0:
        return " AND created_at >= %s", [datetime.now(timezone.utc) - timedelta(days=days)]
    return "", []


@router.get("/overview")
def overview(days: int = 7, request: Request = None):
    p = _require_admin(request)
    tid = p.tenant_id
    tc, ta = _where(days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM kb_query_log WHERE tenant_id=%s{tc}", [tid] + ta)
            total_qa = cur.fetchone()[0]
            cur.execute(f"SELECT count(*) FROM kb_ingest_cost_log WHERE tenant_id=%s{tc}", [tid] + ta)
            uploads = cur.fetchone()[0]
            cur.execute(f"SELECT count(*) FROM kb_query_log WHERE tenant_id=%s AND rerank_used=true{tc}", [tid] + ta)
            rerank = cur.fetchone()[0]
            cur.execute(f"SELECT count(DISTINCT user_id) FROM kb_query_log WHERE tenant_id=%s{tc}", [tid] + ta)
            active = cur.fetchone()[0]
            cur.execute(f"SELECT outcome, count(*) FROM kb_chat_log WHERE tenant_id=%s{tc} GROUP BY outcome", [tid] + ta)
            outcomes = {r[0]: r[1] for r in cur.fetchall()}
    chats = sum(outcomes.values())
    answered = outcomes.get("answered", 0)
    return {
        "days": days,
        "total_qa": total_qa,
        "chats": chats,
        "answered": answered,
        "no_result": outcomes.get("no_result", 0),
        "error": outcomes.get("error", 0),
        "success_rate": round(answered / chats, 3) if chats else None,
        "active_users": active,
        "uploads": uploads,
        "rerank_uses": rerank,
    }


@router.get("/top-queries")
def top_queries(days: int = 7, limit: int = 20, request: Request = None):
    p = _require_admin(request)
    tc, ta = _where(days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT query_norm, count(*) c FROM kb_query_log
                    WHERE tenant_id=%s{tc} AND query_norm IS NOT NULL AND query_norm <> ''
                    GROUP BY query_norm ORDER BY c DESC LIMIT %s""",
                [p.tenant_id] + ta + [limit],
            )
            rows = cur.fetchall()
    return [{"query": r[0], "count": r[1]} for r in rows]


@router.get("/users")
def users_usage(days: int = 7, request: Request = None):
    p = _require_admin(request)
    tid = p.tenant_id
    tc, ta = _where(days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT user_id, count(*) FROM kb_query_log WHERE tenant_id=%s{tc} GROUP BY user_id", [tid] + ta)
            q = {str(r[0]): r[1] for r in cur.fetchall() if r[0]}
            cur.execute(f"SELECT user_id, count(*) FROM kb_chat_log WHERE tenant_id=%s{tc} GROUP BY user_id", [tid] + ta)
            ch = {str(r[0]): r[1] for r in cur.fetchall() if r[0]}
            cur.execute(f"SELECT owner_user_id, count(*) FROM kb_file WHERE tenant_id=%s{tc} GROUP BY owner_user_id", [tid] + ta)
            up = {str(r[0]): r[1] for r in cur.fetchall() if r[0]}
            uids = set(q) | set(ch) | set(up)
            ext_map = {}
            if uids:
                cur.execute("SELECT id, external_id FROM kb_user WHERE id = ANY(%s)", (list(uids),))
                ext_map = {str(r[0]): r[1] for r in cur.fetchall()}
    out = [{
        "userId": uid, "externalId": ext_map.get(uid, uid[:8]),
        "queries": q.get(uid, 0), "chats": ch.get(uid, 0), "uploads": up.get(uid, 0),
    } for uid in uids]
    out.sort(key=lambda x: x["queries"] + x["chats"] + x["uploads"], reverse=True)
    return out


@router.get("/models")
def models_usage(days: int = 7, request: Request = None):
    p = _require_admin(request)
    tc, ta = _where(days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT model, count(*) FROM kb_chat_log WHERE tenant_id=%s{tc} AND model IS NOT NULL GROUP BY model", [p.tenant_id] + ta)
            llm = [{"model": r[0], "type": "llm", "calls": r[1]} for r in cur.fetchall()]
            cur.execute(f"SELECT model, count(*) FROM kb_ingest_cost_log WHERE tenant_id=%s{tc} AND model IS NOT NULL GROUP BY model", [p.tenant_id] + ta)
            emb = [{"model": r[0], "type": "embedding", "calls": r[1]} for r in cur.fetchall()]
    return llm + emb


@router.get("/users/{user_id}/chats")
def user_chats(user_id: str, days: int = 30, limit: int = 50, request: Request = None):
    """某成员的问答记录（问题 + 答案 + 结果 + 时间）。tenant 隔离：仅本租户该用户的 chat。"""
    p = _require_admin(request)
    tc, ta = _where(days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, query, answer, model, outcome, hits, latency_ms, created_at
                    FROM kb_chat_log WHERE tenant_id=%s AND user_id=%s{tc}
                    ORDER BY created_at DESC LIMIT %s""",
                [p.tenant_id, user_id] + ta + [limit],
            )
            rows = cur.fetchall()
    return [{
        "id": r[0], "query": r[1], "answer": r[2], "model": r[3], "outcome": r[4],
        "hits": r[5], "latencyMs": r[6], "createdAt": r[7].isoformat() if r[7] else None,
    } for r in rows]


@router.get("/users/{user_id}/conversations")
def user_conversations(user_id: str, limit: int = 30, request: Request = None):
    """管理员查看某成员的**会话**（按 session 分组，含消息）。tenant 隔离：仅本租户该用户。"""
    p = _require_admin(request)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, title, updated_at FROM kb_conversation
                   WHERE user_id=%s AND tenant_id=%s ORDER BY updated_at DESC LIMIT %s""",
                (user_id, p.tenant_id, limit),
            )
            convs = cur.fetchall()
            out = []
            for cid, title, updated_at in convs:
                cur.execute(
                    """SELECT role, content, meta, created_at FROM kb_message
                       WHERE conversation_id=%s ORDER BY id""",
                    (cid,),
                )
                msgs = [{
                    "role": r[0], "content": r[1], "meta": r[2],
                    "createdAt": r[3].isoformat() if r[3] else None,
                } for r in cur.fetchall()]
                out.append({
                    "id": str(cid), "title": title or "新对话",
                    "updatedAt": updated_at.isoformat() if updated_at else None,
                    "messages": msgs,
                })
    return out

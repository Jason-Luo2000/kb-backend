"""问答会话（用户级）：新建/列表/详情/重命名/删除。每用户看自己的会话。
非 admin——普通用户即可管理自己的会话历史。所有权：user_id + tenant 匹配，他人→404。
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row

from app.db import get_conn
from app.middleware.auth import get_principal, verify_api_key

router = APIRouter(prefix="/v1/chat/conversations", dependencies=[Depends(verify_api_key)])


def _own(conn, cur, conv_id: str, principal) -> bool:
    """会话是否属于调用者（user + tenant）。"""
    cur.execute(
        "SELECT 1 FROM kb_conversation WHERE id=%s AND user_id=%s AND tenant_id=%s",
        (conv_id, principal.user_id, principal.tenant_id),
    )
    return cur.fetchone() is not None


@router.post("")
def create_conversation(body: dict, request: Request):
    p = get_principal(request)
    cid = str(uuid.uuid4())
    title = (body.get("title") or "").strip() or None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_conversation(id, tenant_id, user_id, title) VALUES (%s,%s,%s,%s)",
                (cid, p.tenant_id, p.user_id, title),
            )
    return {"id": cid, "title": title}


@router.get("")
def list_conversations(request: Request):
    p = get_principal(request)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT c.id, c.title, c.updated_at,
                          (SELECT content FROM kb_message m WHERE m.conversation_id = c.id
                           ORDER BY m.id DESC LIMIT 1) AS preview
                   FROM kb_conversation c
                   WHERE c.user_id = %s AND c.tenant_id = %s
                   ORDER BY c.updated_at DESC""",
                (p.user_id, p.tenant_id),
            )
            rows = cur.fetchall()
    return [{
        "id": str(r["id"]),
        "title": r["title"] or "新对话",
        "updatedAt": r["updated_at"].isoformat() if r["updated_at"] else None,
        "preview": (r["preview"] or "")[:60],
    } for r in rows]


@router.get("/{conv_id}")
def get_conversation(conv_id: str, request: Request):
    p = get_principal(request)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if not _own(conn, cur, conv_id, p):
                raise HTTPException(status_code=404, detail="KB_CONVERSATION_NOT_FOUND")
            cur.execute("SELECT id, title FROM kb_conversation WHERE id=%s", (conv_id,))
            c = cur.fetchone()
            cur.execute(
                """SELECT role, content, meta, created_at FROM kb_message
                   WHERE conversation_id=%s ORDER BY id""",
                (conv_id,),
            )
            msgs = cur.fetchall()
    return {
        "id": conv_id,
        "title": c["title"],
        "messages": [{
            "role": m["role"], "content": m["content"], "meta": m["meta"],
            "createdAt": m["created_at"].isoformat() if m["created_at"] else None,
        } for m in msgs],
    }


@router.patch("/{conv_id}")
def rename_conversation(conv_id: str, body: dict, request: Request):
    p = get_principal(request)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE kb_conversation SET title=%s, updated_at=now() WHERE id=%s AND user_id=%s AND tenant_id=%s",
                (title, conv_id, p.user_id, p.tenant_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="KB_CONVERSATION_NOT_FOUND")
    return {"ok": True}


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str, request: Request):
    p = get_principal(request)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kb_conversation WHERE id=%s AND user_id=%s AND tenant_id=%s",
                (conv_id, p.user_id, p.tenant_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="KB_CONVERSATION_NOT_FOUND")
    return {"ok": True}

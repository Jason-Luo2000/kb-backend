"""管理端授权：PUT/DELETE /v1/acl（grant/revoke）。
红队：grant 是高危动作，**禁止作为 LLM 工具**（防 prompt-injection 提权），仅 admin UI/SDK 调用。
仅该 kb 的 admin+（或租户 owner/admin）可授权。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.authz import is_kb_admin, resolve as resolve_authz
from app.db import get_conn
from app.middleware.auth import get_principal, verify_api_key

router = APIRouter(prefix="/v1/acl", dependencies=[Depends(verify_api_key)])


def _kb_tenant(kb_id: str) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM kb_kb WHERE id=%s", (kb_id,))
            row = cur.fetchone()
            return str(row[0]) if row else None


@router.put("")
def grant(body: dict, request: Request):
    principal = get_principal(request)
    kb_id = body["kbId"]
    user_id = body["userId"]
    role = body.get("role", "viewer")
    if role not in ("viewer", "editor", "admin"):
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    # kb 必须在调用者租户内（不泄露跨租户存在性）
    if _kb_tenant(kb_id) != principal.tenant_id:
        raise HTTPException(status_code=404, detail="KB_KB_NOT_FOUND")
    decision = resolve_authz(principal)
    if not is_kb_admin(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    grant_id = str(uuid.uuid4())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_grant(grant_id,kb_id,user_id,role,granted_by,expires_at,source)
                   VALUES (%s,%s,%s,%s,%s,%s,'explicit')
                   ON CONFLICT (kb_id,user_id) DO UPDATE
                   SET role=EXCLUDED.role, revoked_at=NULL,
                       expires_at=EXCLUDED.expires_at, granted_by=EXCLUDED.granted_by""",
                (grant_id, kb_id, user_id, role, principal.user_id, body.get("expiresAt")),
            )
    return {"ok": True, "kbId": kb_id, "userId": user_id, "role": role}


@router.delete("")
def revoke(body: dict, request: Request):
    principal = get_principal(request)
    kb_id = body["kbId"]
    user_id = body["userId"]
    if _kb_tenant(kb_id) != principal.tenant_id:
        raise HTTPException(status_code=404, detail="KB_KB_NOT_FOUND")
    decision = resolve_authz(principal)
    if not is_kb_admin(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE kb_grant SET revoked_at=now() WHERE kb_id=%s AND user_id=%s AND revoked_at IS NULL",
                (kb_id, user_id),
            )
    return {"ok": True}

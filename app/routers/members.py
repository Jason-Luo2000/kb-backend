"""成员管理（超管/租户 admin）：列/建/改成员（含部门标签）+ 看某成员可见库 + 批量授权/撤销。

按成员 + 部门标签方案（非组级 ACL）：部门仅用于筛选；可见库仍按人显式 grant + 角色/可见性派生。
红队：授权/建成员是高危，**禁止 LLM 工具**，仅 tenant owner/admin UI/SDK。
"""
import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row

from app.authz import is_tenant_admin
from app.db import get_conn
from app.middleware.auth import audit, get_principal, verify_api_key

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(verify_api_key)])

_ROLES = ("owner", "admin", "editor", "viewer")


def _require_admin(request: Request):
    p = get_principal(request)
    if not is_tenant_admin(p):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_ADMIN")
    return p


# ============ 成员 CRUD ============
@router.get("/users")
def list_users(department: str | None = None, request: Request = None):
    p = _require_admin(request)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if department:
                cur.execute(
                    """SELECT u.id, u.external_id, u.name, ut.role, ut.department, u.created_at
                       FROM kb_user u JOIN kb_user_tenant ut ON ut.user_id = u.id
                       WHERE ut.tenant_id = %s AND ut.department = %s
                       ORDER BY ut.department, u.external_id""",
                    (p.tenant_id, department),
                )
            else:
                cur.execute(
                    """SELECT u.id, u.external_id, u.name, ut.role, ut.department, u.created_at
                       FROM kb_user u JOIN kb_user_tenant ut ON ut.user_id = u.id
                       WHERE ut.tenant_id = %s
                       ORDER BY ut.department NULLS LAST, u.external_id""",
                    (p.tenant_id,),
                )
            rows = cur.fetchall()
    # 各成员可见库数（授权 + 角色派生）
    out = []
    for r in rows:
        kbs = _user_effective_kbs(p.tenant_id, str(r["id"]))
        out.append({
            "userId": str(r["id"]),
            "externalId": r["external_id"],
            "name": r["name"],
            "role": r["role"],
            "department": r["department"],
            "kbCount": len(kbs),
            "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return out


@router.get("/users/departments")
def list_departments(request: Request):
    """列出本租户出现过的部门（筛选用）。"""
    p = _require_admin(request)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT department FROM kb_user_tenant WHERE tenant_id=%s AND department IS NOT NULL ORDER BY department",
                (p.tenant_id,),
            )
            return [r[0] for r in cur.fetchall()]


@router.post("/users")
def create_user(body: dict, request: Request):
    """建成员：{externalId, name?, department?, role="viewer"} → 返 {userId, apiKey}（apiKey 仅此一次）。"""
    p = _require_admin(request)
    external_id = (body.get("externalId") or "").strip()
    if not external_id:
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    role = body.get("role", "viewer")
    if role not in _ROLES:
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    uid = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_user(id, external_id, name) VALUES (%s,%s,%s)
                   ON CONFLICT (external_id) DO UPDATE SET name=COALESCE(kb_user.name, EXCLUDED.name)
                   RETURNING id""",
                (uid, external_id, body.get("name")),
            )
            uid = str(cur.fetchone()[0])
            cur.execute(
                """INSERT INTO kb_user_tenant(user_id, tenant_id, role, department)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (user_id, tenant_id) DO UPDATE SET role=EXCLUDED.role, department=EXCLUDED.department""",
                (uid, p.tenant_id, role, body.get("department")),
            )
            cur.execute(
                """INSERT INTO kb_api_key(id, tenant_id, user_id, key_hash, scopes)
                   VALUES (%s,%s,%s,%s,'["*"]'::jsonb)""",
                (str(uuid.uuid4()), p.tenant_id, uid, key_hash),
            )
    audit("MEMBER_CREATE", request, result="ok", detail={"user_id": uid, "role": role})
    return {"userId": uid, "apiKey": token}


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: dict, request: Request):
    """改成员：{name?, department?, role?}。name 在 kb_user；department/role 在 kb_user_tenant。"""
    p = _require_admin(request)
    tenant_sets, args = [], []
    if "department" in body:
        tenant_sets.append("department=%s"); args.append(body["department"])
    if "role" in body:
        if body["role"] not in _ROLES:
            raise HTTPException(status_code=400, detail="KB_VALIDATION")
        tenant_sets.append("role=%s"); args.append(body["role"])
    with get_conn() as conn:
        with conn.cursor() as cur:
            if tenant_sets:
                args += [user_id, p.tenant_id]
                cur.execute(
                    f"""UPDATE kb_user_tenant SET {', '.join(tenant_sets)}
                        WHERE user_id=%s AND tenant_id=%s RETURNING 1""",
                    args,
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="KB_USER_NOT_FOUND")
            else:
                cur.execute(
                    "SELECT 1 FROM kb_user_tenant WHERE user_id=%s AND tenant_id=%s",
                    (user_id, p.tenant_id),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="KB_USER_NOT_FOUND")
            if "name" in body:
                cur.execute("UPDATE kb_user SET name=%s WHERE id=%s", (body["name"], user_id))
    audit("MEMBER_UPDATE", request, result="ok", detail={"user_id": user_id})
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, request: Request):
    """移除成员（删本租户成员关系 + 其授权 + api_key；不删跨租户身份）。"""
    p = _require_admin(request)
    if user_id == p.user_id:
        raise HTTPException(status_code=400, detail="KB_CANNOT_REMOVE_SELF")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kb_user_tenant WHERE user_id=%s AND tenant_id=%s RETURNING 1",
                (user_id, p.tenant_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="KB_USER_NOT_FOUND")
            cur.execute("UPDATE kb_grant SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
            cur.execute("UPDATE kb_api_key SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
    audit("MEMBER_DELETE", request, result="ok", detail={"user_id": user_id})
    return {"ok": True}


# ============ 某成员的可见库 + 批量授权 ============
def _user_effective_kbs(tenant_id: str, user_id: str) -> list[dict]:
    """该成员当前能访问的库：角色/可见性派生 + 显式授权。每条带 source + can_revoke。"""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT role FROM kb_user_tenant WHERE user_id=%s AND tenant_id=%s",
                (user_id, tenant_id),
            )
            trow = cur.fetchone()
            if not trow:
                return []
            tenant_role = trow["role"]
            cur.execute(
                """SELECT kb_id, role FROM kb_grant
                   WHERE user_id=%s AND revoked_at IS NULL
                     AND (expires_at IS NULL OR expires_at > now())""",
                (user_id,),
            )
            grants = {str(r["kb_id"]): r["role"] for r in cur.fetchall()}
            cur.execute("SELECT id, name, visibility FROM kb_kb WHERE tenant_id=%s ORDER BY name", (tenant_id,))
            kbs = cur.fetchall()
    out = []
    for kb in kbs:
        kid = str(kb["id"])
        role_derived = (
            tenant_role in ("owner", "admin")
            or (tenant_role == "editor" and kb["visibility"] in ("team", "tenant"))
        )
        has_grant = kid in grants
        if not (role_derived or has_grant):
            continue
        if has_grant and role_derived:
            source = "授权(提升)"
        elif has_grant:
            source = "授权"
        else:
            source = "角色/可见性"
        eff_role = grants[kid] if has_grant else ("admin" if tenant_role in ("owner", "admin") else "viewer")
        out.append({
            "kbId": kid, "name": kb["name"], "visibility": kb["visibility"],
            "role": eff_role, "source": source, "canRevoke": has_grant,
        })
    return out


@router.get("/users/{user_id}/kbs")
def user_kbs(user_id: str, request: Request):
    p = _require_admin(request)
    return _user_effective_kbs(p.tenant_id, user_id)


@router.post("/users/{user_id}/kbs")
def bulk_grant(user_id: str, body: dict, request: Request):
    """批量授权：{kbIds:[], role="viewer"}。tenant admin/owner 对本租户所有库有 admin 权。"""
    p = _require_admin(request)
    role = body.get("role", "viewer")
    if role not in ("viewer", "editor", "admin"):
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    kb_ids = body.get("kbIds") or []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for kb_id in kb_ids:
                cur.execute(
                    """INSERT INTO kb_grant(grant_id,kb_id,user_id,role,granted_by,source)
                       VALUES (%s,%s,%s,%s,%s,'explicit')
                       ON CONFLICT (kb_id,user_id) DO UPDATE
                       SET role=EXCLUDED.role, revoked_at=NULL, granted_by=EXCLUDED.granted_by""",
                    (str(uuid.uuid4()), kb_id, user_id, role, p.user_id),
                )
    audit("MEMBER_GRANT", request, result="ok", detail={"user_id": user_id, "kb_count": len(kb_ids)})
    return {"ok": True, "granted": len(kb_ids)}

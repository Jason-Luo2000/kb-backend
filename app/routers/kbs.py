"""知识库管理：GET/POST /v1/kbs（T9：租户内 + ACL，返回每 kb 的 role）。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row

from app.authz import resolve as resolve_authz
from app.db import get_conn
from app.middleware.auth import get_principal, verify_api_key

router = APIRouter(prefix="/v1/kbs", dependencies=[Depends(verify_api_key)])


@router.get("")
def list_kbs(request: Request):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    if not decision.allowed_kb_ids:
        return []
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT k.id, k.name, k.description, k.created_at, k.visibility,
                          (SELECT count(*) FROM kb_file_kb fk WHERE fk.kb_id = k.id) AS doc_count
                   FROM kb_kb k
                   WHERE k.tenant_id = %s AND k.id = ANY(%s)
                   ORDER BY k.created_at""",
                (principal.tenant_id, decision.allowed_kb_ids),
            )
            rows = cur.fetchall()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "docCount": r["doc_count"],
            "role": decision.kb_roles.get(str(r["id"]), "viewer"),
            "visibility": r["visibility"],
        }
        for r in rows
    ]


@router.post("")
def create_kb(body: dict, request: Request):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    principal = get_principal(request)
    kid = str(uuid.uuid4())
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_kb(id,tenant_id,name,description,visibility,owner_id)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (
                        kid,
                        principal.tenant_id,
                        name,
                        body.get("description"),
                        body.get("visibility", "team"),
                        principal.user_id,
                    ),
                )
    except Exception:  # noqa: BLE001  UNIQUE(tenant_id,name) 冲突
        raise HTTPException(status_code=409, detail="KB_VALIDATION") from None
    return {"id": kid, "name": name}

"""知识库管理：GET/POST/PATCH /v1/kbs（T9：租户内 + ACL，返回每 kb 的 role）。
C：parser_config（分块配置）随 KB 存。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.authz import can_write, is_tenant_admin, resolve as resolve_authz
from app.db import get_conn
from app.middleware.auth import get_principal, verify_api_key

router = APIRouter(prefix="/v1/kbs", dependencies=[Depends(verify_api_key)])


def _coerce_parser_config(body: dict):
    """body 里 parser_config / parserConfig（dict 或 JSON 字符串）→ dict 或 None。"""
    pcfg = body.get("parserConfig", body.get("parser_config"))
    if isinstance(pcfg, str):
        import json

        try:
            pcfg = json.loads(pcfg)
        except Exception:  # noqa: BLE001
            return None
    return pcfg if isinstance(pcfg, dict) and pcfg else None


@router.get("")
def list_kbs(request: Request):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    if not decision.allowed_kb_ids:
        return []
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT k.id, k.name, k.description, k.created_at, k.visibility, k.parser_config,
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
            "parserConfig": r["parser_config"],
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
    pcfg = _coerce_parser_config(body)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if pcfg:
                    cur.execute(
                        """INSERT INTO kb_kb(id,tenant_id,name,description,visibility,owner_id,parser_config)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (kid, principal.tenant_id, name, body.get("description"),
                         body.get("visibility", "team"), principal.user_id, Json(pcfg)),
                    )
                else:
                    cur.execute(
                        """INSERT INTO kb_kb(id,tenant_id,name,description,visibility,owner_id)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (kid, principal.tenant_id, name, body.get("description"),
                         body.get("visibility", "team"), principal.user_id),
                    )
    except Exception:  # noqa: BLE001  UNIQUE(tenant_id,name) 冲突
        raise HTTPException(status_code=409, detail="KB_VALIDATION") from None
    return {"id": kid, "name": name}


@router.patch("/{kb_id}")
def update_kb(kb_id: str, body: dict, request: Request):
    """C：更新 KB 元信息 / parser_config（editor+）。"""
    principal = get_principal(request)
    decision = resolve_authz(principal)
    if kb_id not in decision.allowed_kb_ids or not can_write(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    sets: list[str] = []
    args: list = []
    for f in ("name", "description", "visibility"):
        if f in body and body[f] is not None:
            sets.append(f"{f}=%s")
            args.append(body[f])
    pcfg = _coerce_parser_config(body)
    if pcfg is not None:
        sets.append("parser_config=%s")
        args.append(Json(pcfg))
    if not sets:
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    args.append(kb_id)
    args.append(principal.tenant_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""UPDATE kb_kb SET {', '.join(sets)} WHERE id=%s AND tenant_id=%s""",
                args,
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="KB_NOT_FOUND")
    return {"ok": True}


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, request: Request):
    """删知识库（tenant owner/admin）。CASCADE 清 kb_file_kb 链接 + kb_grant；文件本身保留（在个人库/其它库）。"""
    principal = get_principal(request)
    if not is_tenant_admin(principal):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_ADMIN")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kb_kb WHERE id=%s AND tenant_id=%s RETURNING 1",
                (kb_id, principal.tenant_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="KB_NOT_FOUND")
    return {"ok": True}

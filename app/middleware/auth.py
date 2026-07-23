"""认证（API-key → Principal）+ 审计 + 限流——红队合规底线：MVP 不可后置。

T9：单一共享 key 升级为 API-key → (tenant_id,user_id) 解析。token 经 sha256 查 kb_api_key，
命中且未撤销 → Principal 挂 request.state。JWT/SSO 见后期 T25。
"""
import hashlib
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from psycopg.rows import dict_row
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.db import get_conn

limiter = Limiter(key_func=get_remote_address, default_limits=["600/minute"])


@dataclass
class Principal:
    """请求级身份：T9 多租户的根。authz.AuthzEngine 据此解析授权。"""

    tenant_id: str
    user_id: str
    scopes: list[str] = field(default_factory=list)


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    api_key = request.headers.get("x-kb-api-key", "")
    return auth[7:] if auth.lower().startswith("bearer ") else api_key


def verify_api_key(request: Request) -> Principal:
    """router 级认证依赖：token → Principal，挂 request.state.principal。"""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="KB_UNAUTHORIZED")
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT tenant_id, user_id, scopes
                   FROM kb_api_key
                   WHERE key_hash = %s AND revoked_at IS NULL""",
                (key_hash,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="KB_UNAUTHORIZED")
    principal = Principal(
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
        scopes=row["scopes"] or [],
    )
    request.state.principal = principal
    return principal


def get_principal(request: Request) -> Principal:
    """handler 注入用：读取 verify_api_key 已挂的 Principal。"""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="KB_UNAUTHORIZED")
    return principal


def audit(action: str, request: Request | None = None, principal: Principal | None = None, **fields) -> None:
    """best-effort 审计落库（append-only，哈希链/trust anchor 中期 T15 补）。
    principal 显式传入优先；否则从 request.state.principal 取。"""
    if principal is None and request is not None:
        principal = getattr(request.state, "principal", None)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_audit_log(tenant_id,user_id,action,kb_ids,query_text,hit_chunk_ids,
                       result,request_id,ip,user_agent)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        principal.tenant_id if principal else None,
                        principal.user_id if principal else None,
                        action,
                        fields.get("kb_ids"),
                        fields.get("query"),
                        fields.get("hits"),
                        fields.get("result", "ok"),
                        fields.get("request_id"),
                        fields.get("ip"),
                        fields.get("ua"),
                    ),
                )
    except Exception:
        pass

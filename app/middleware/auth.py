"""认证（API key）+ 审计 + 限流——红队合规底线：MVP 不可后置。"""
from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.db import get_conn

limiter = Limiter(key_func=get_remote_address, default_limits=["600/minute"])


def verify_api_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    api_key = request.headers.get("x-kb-api-key", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else api_key
    if token != settings.kb_api_key:
        raise HTTPException(status_code=401, detail="KB_UNAUTHORIZED")
    return token


def audit(action: str, **fields) -> None:
    """best-effort 审计落库（append-only，哈希链/trust anchor 中期补）。"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_audit_log(action,kb_ids,query_text,hit_chunk_ids,result,request_id,ip,user_agent)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
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

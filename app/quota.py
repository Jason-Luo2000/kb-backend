"""摄入配额 + 用量计量（T15，review #29）。

上传前预检 docs/bytes（monthly 窗），超额 → 413 KB_QUOTA_EXCEEDED。
用量：kb_usage 当月计数（meter_ingest 在 kb_file INSERT 同事务 upsert）；无行时回退聚合 kb_file。
0 = 不限。
"""
from datetime import datetime, timezone

from app.config import settings
from app.db import get_conn


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_limits(tenant_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT max_docs, max_bytes, period FROM kb_quota WHERE tenant_id=%s", (tenant_id,))
            row = cur.fetchone()
    if row:
        return {"max_docs": row[0], "max_bytes": row[1], "period": row[2]}
    return {"max_docs": settings.default_quota_docs, "max_bytes": settings.default_quota_bytes, "period": "monthly"}


def get_usage(tenant_id: str) -> dict:
    period = _month()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_count, bytes FROM kb_usage WHERE tenant_id=%s AND period=%s",
                (tenant_id, period),
            )
            row = cur.fetchone()
            if row:
                return {"period": period, "doc_count": row[0], "bytes": row[1]}
            # 回退：聚合 kb_file 当月（size_bytes 已落库）
            cur.execute(
                "SELECT count(*), coalesce(sum(size_bytes),0) FROM kb_file "
                "WHERE tenant_id=%s AND created_at >= date_trunc('month', now())",
                (tenant_id,),
            )
            dc, by = cur.fetchone()
    return {"period": period, "doc_count": dc, "bytes": by}


def check_quota(tenant_id: str, size_bytes: int) -> tuple[bool, str | None, dict]:
    """返回 (ok, reason, info)。reason='docs'|'bytes'|None。0 = 不限。"""
    limits = get_limits(tenant_id)
    usage = get_usage(tenant_id)
    over_docs = limits["max_docs"] and usage["doc_count"] + 1 > limits["max_docs"]
    over_bytes = limits["max_bytes"] and usage["bytes"] + size_bytes > limits["max_bytes"]
    ok = not (over_docs or over_bytes)
    reason = "docs" if over_docs else ("bytes" if over_bytes else None)
    return ok, reason, {"limits": limits, "usage": usage, "incoming_bytes": size_bytes}


def meter_ingest(conn, tenant_id: str, size_bytes: int) -> None:
    """新摄成功后计量（同 kb_file INSERT 事务，原子）。kb_usage 月度 upsert。"""
    period = _month()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kb_usage(tenant_id,period,doc_count,bytes) VALUES(%s,%s,1,%s)
               ON CONFLICT(tenant_id,period) DO UPDATE
               SET doc_count=kb_usage.doc_count+1, bytes=kb_usage.bytes+EXCLUDED.bytes""",
            (tenant_id, period, size_bytes),
        )


def meter_delete(conn, tenant_id: str, size_bytes: int) -> None:
    """删文件后计量回退（同 DELETE 事务）。kb_usage 当月 -1/-bytes，GREATEST 防负
    （跨月删除时本月计数本不含它，clamp 在 0 不致错）。"""
    period = _month()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kb_usage(tenant_id,period,doc_count,bytes) VALUES(%s,%s,0,0) ON CONFLICT DO NOTHING",
            (tenant_id, period),
        )
        cur.execute(
            """UPDATE kb_usage SET doc_count=GREATEST(doc_count-1,0),
               bytes=GREATEST(bytes-%s,0) WHERE tenant_id=%s AND period=%s""",
            (size_bytes or 0, tenant_id, period),
        )

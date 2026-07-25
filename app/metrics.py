"""T16 监控：Prometheus 指标 + RED 中间件 + 业务采集。

review #30 + A8（upload→indexed p95<5min）。/metrics 无鉴权（内部采集约定，部署侧绑内网）。
ingest SLO = ingest_file 时长（同步模型，docs.py 计时）；查询侧 path_a_completed_rate 直方图；
廉价 SQL gauge（检索 p95、ingest 计数/tokens、quota、SEC_VIOLATION）。
昂贵项（audit verify / reconcile drift）走已有 admin 端点按需，不进 /metrics（防慢查拖垮抓取）。
"""
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

# ---- HTTP RED（rate/errors/duration，中间件计数）----
REQUEST_COUNT = Counter("kb_http_requests_total", "HTTP requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("kb_request_duration_seconds", "HTTP request duration", ["method", "endpoint"])

# ---- SLO ----
INGEST_DURATION = Histogram(
    "kb_ingest_duration_seconds",
    "upload→indexed (ingest_file) 时长，A8 SLO p95<5min",
    ["tenant", "outcome"],
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
)
PATH_A_RATE = Histogram(
    "kb_path_a_completed_rate",
    "路 A 完成率，查询侧 SLO（§D.6 <50% 告警）",
    buckets=(0, 0.1, 0.25, 0.5, 0.7, 0.9, 1),
)

# ---- 业务 Gauge（tenant 标签，collect_business_metrics 填）----
RETRIEVAL_P95 = Gauge("kb_retrieval_p95_ms", "检索 p95 延迟 ms（近 1h）", ["tenant"])
INGEST_COUNT = Gauge("kb_ingest_count", "摄入计数（近 1h）", ["tenant"])
INGEST_TOKENS = Gauge("kb_ingest_tokens", "摄入 tokens（近 1h）", ["tenant"])
QUOTA_DOCS = Gauge("kb_quota_docs", "配额用量 doc_count（当月）", ["tenant"])
QUOTA_BYTES = Gauge("kb_quota_bytes", "配额用量 bytes（当月）", ["tenant"])
SEC_VIOLATIONS = Gauge("kb_sec_violations", "SEC_VIOLATION 计数（近 1h）", ["tenant"])


def _endpoint(request) -> str:
    """路由模板（避免 UUID 爆基数）；未匹配→unmatched。"""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path == "/metrics":  # 跳过抓取自身
            return await call_next(request)
        t0 = time.perf_counter()
        status = "500"
        try:
            resp = await call_next(request)
            status = str(resp.status_code)
            return resp
        except Exception:
            status = "500"
            raise
        finally:
            elapsed = time.perf_counter() - t0
            ep = _endpoint(request)
            REQUEST_COUNT.labels(request.method, ep, status).inc()
            REQUEST_LATENCY.labels(request.method, ep).observe(elapsed)


def collect_business_metrics() -> None:
    """best-effort 短窗 SQL 聚合 set 业务 Gauge。无 DB 时吞错（/metrics 仍返 RED）。"""
    from app.db import get_conn

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT tenant_id,
                       COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms), 0)
                       FROM kb_query_log
                       WHERE created_at > now() - interval '1 hour' AND tenant_id IS NOT NULL
                       GROUP BY tenant_id"""
                )
                for tid, p95 in cur.fetchall():
                    RETRIEVAL_P95.labels(str(tid)).set(float(p95))
                cur.execute(
                    """SELECT tenant_id, COUNT(*), COALESCE(SUM(tokens), 0)
                       FROM kb_ingest_cost_log
                       WHERE created_at > now() - interval '1 hour' AND tenant_id IS NOT NULL
                       GROUP BY tenant_id"""
                )
                for tid, cnt, toks in cur.fetchall():
                    INGEST_COUNT.labels(str(tid)).set(float(cnt))
                    INGEST_TOKENS.labels(str(tid)).set(float(toks))
                cur.execute(
                    "SELECT tenant_id, doc_count, bytes FROM kb_usage WHERE period = to_char(now(),'YYYY-MM')"
                )
                for tid, dc, by in cur.fetchall():
                    QUOTA_DOCS.labels(str(tid)).set(float(dc))
                    QUOTA_BYTES.labels(str(tid)).set(float(by))
                cur.execute(
                    """SELECT tenant_id, COUNT(*) FROM kb_audit_log
                       WHERE created_at > now() - interval '1 hour'
                         AND result != 'ok' AND tenant_id IS NOT NULL
                       GROUP BY tenant_id"""
                )
                for tid, cnt in cur.fetchall():
                    SEC_VIOLATIONS.labels(str(tid)).set(float(cnt))
    except Exception:  # noqa: BLE001  无 DB 时 /metrics 仍返 RED 指标
        pass


def metrics_body() -> tuple[bytes, str]:
    collect_business_metrics()
    return generate_latest(), CONTENT_TYPE_LATEST

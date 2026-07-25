"""T16 metrics 单测（Starlette TestClient，无需 DB/ES/MinIO）。
lifespan 吞 bootstrap 错；collect_business_metrics best-effort 吞 DB 错→/metrics 仍返 RED。
运行：.venv/bin/pytest tests/test_metrics.py -q"""
from starlette.testclient import TestClient

from app.main import app


def test_healthz():
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"ok": True}


def test_metrics_has_red_counter():
    with TestClient(app) as c:
        c.get("/healthz")  # 触发一次请求让 RED 中间件计数
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "kb_http_requests_total" in r.text
        assert "/healthz" in r.text  # endpoint 标签（路由模板）


def test_metrics_registers_slo_and_gauges():
    """指标注册即可见（HELP/TYPE 行），不依赖是否有样本。"""
    with TestClient(app) as c:
        body = c.get("/metrics").text
        for name in (
            "kb_ingest_duration_seconds",
            "kb_path_a_completed_rate",
            "kb_retrieval_p95_ms",
            "kb_quota_docs",
        ):
            assert name in body, name


def test_readyz_returns_components():
    with TestClient(app) as c:
        body = c.get("/readyz").json()
        assert set(["db", "es", "minio"]).issubset(body.keys())

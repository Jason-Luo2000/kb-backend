"""T16 /metrics live 测试（需 uvicorn 在跑）。
起 .venv/bin/uvicorn app.main:app --port 8001；
KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=kb_dev_api_key .venv/bin/python scripts/metrics_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

import httpx
from kb_sdk import KBClient

BASE = os.getenv("KB_BACKEND_URL", "http://localhost:8001")
KEY = os.getenv("KB_API_KEY", "kb_dev_api_key")
USER = os.getenv("KB_USER_ID", "u_demo")


def main():
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    h = httpx.Client(timeout=60)
    check("/healthz 200", h.get(BASE + "/healthz").status_code == 200)

    # 触发一次 ingest 填 ingest 直方图 + 一次检索填 path_a 直方图
    c = KBClient(BASE, KEY, USER)
    kb = c.create_kb("metrics-test-" + os.urandom(3).hex())
    f = tempfile.NamedTemporaryFile("wb", suffix=".md", delete=False)
    f.write(("# m\nmetricsmarker " * 20).encode())
    f.close()
    try:
        c.upload(kb["id"], f.name)
        c.search("metricsmarker", knowledge_base_ids=[kb["id"]])  # 填 path_a 直方图
    finally:
        os.unlink(f.name)

    m = h.get(BASE + "/metrics").text
    check("RED 计数有样本", "kb_http_requests_total{" in m)
    check("ingest SLO 直方图有样本", "kb_ingest_duration_seconds_bucket{" in m)
    check("path_a 直方图有样本", "kb_path_a_completed_rate_bucket{" in m)
    check("业务 gauge 定义在", "kb_retrieval_p95_ms" in m)

    r = h.get(BASE + "/readyz").json()
    check("/readyz 三组件", all(k in r for k in ("db", "es", "minio")), str(r))
    h.close()

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

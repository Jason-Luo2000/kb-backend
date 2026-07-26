"""FastAPI 入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import bootstrap
from app.config import settings
from app.db import get_conn
from app.middleware.auth import limiter
from app.metrics import MetricsMiddleware, metrics_body
from app.routers import admin, admin_ops, analytics, docs, files, kbs, members, models, search


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        bootstrap.run()
    except Exception as e:  # noqa: BLE001
        # 开发时常有后端先于中间件就绪；打印但不阻塞启动
        print(f"[bootstrap] skipped/failed: {e}")
    yield


app = FastAPI(title="kb-backend", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(MetricsMiddleware)  # T16：HTTP RED（请求计数/延迟/错误）
app.add_middleware(  # 前端 CORS（API-key 经 header，非 cookie → allow_credentials=False）
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/metrics")  # T16：Prometheus 抓取（无鉴权，内部采集；业务 gauge best-effort）
def metrics():
    body, content_type = metrics_body()
    return Response(body, media_type=content_type)


@app.get("/readyz")  # T16：DB/ES/MinIO 探活（best-effort，单组件失败不阻塞整体）
def readyz():
    from app.es import INDEX, get_es
    from app.storage import get_minio

    checks = {}
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception:  # noqa: BLE001
        checks["db"] = "fail"
    try:
        get_es().indices.exists(index=INDEX)
        checks["es"] = "ok"
    except Exception:  # noqa: BLE001
        checks["es"] = "fail"
    try:
        checks["minio"] = "ok" if get_minio().bucket_exists(settings.minio_bucket) else "fail"
    except Exception:  # noqa: BLE001
        checks["minio"] = "fail"
    return checks


app.include_router(kbs.router)
app.include_router(docs.router)
app.include_router(search.router)
app.include_router(admin.router)
app.include_router(admin_ops.router)
app.include_router(models.router)
app.include_router(files.router)
app.include_router(members.router)
app.include_router(analytics.router)

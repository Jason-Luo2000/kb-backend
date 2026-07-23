"""FastAPI 入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import bootstrap
from app.middleware.auth import limiter
from app.routers import docs, kbs, search


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


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.include_router(kbs.router)
app.include_router(docs.router)
app.include_router(search.router)

#!/bin/sh
# 后端容器入口：等 PG 就绪 → bootstrap（建表/ES mapping/MinIO bucket/种身份）→ uvicorn。
# ES/MinIO 的就绪由 compose healthcheck + depends_on 保证；这里只额外兜底 PG。
set -e

echo "[entrypoint] waiting for postgres..."
i=0
while [ "$i" -lt 60 ]; do
    if python -c "from app.config import PG_DSN; import psycopg; psycopg.connect(PG_DSN, connect_timeout=2).close()" 2>/dev/null; then
        echo "[entrypoint] postgres ready"
        break
    fi
    i=$((i + 1))
    echo "[entrypoint] postgres not ready ($i/60), retry in 1s..."
    sleep 1
done

echo "[entrypoint] running bootstrap (idempotent)..."
python -m app.bootstrap || { echo "[entrypoint] bootstrap failed"; exit 1; }

echo "[entrypoint] starting uvicorn on :8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

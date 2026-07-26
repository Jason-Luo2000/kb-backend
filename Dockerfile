FROM python:3.11-slim
WORKDIR /app
# build-essential: lxml/docx 等编译；curl: 容器 healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY app ./app
COPY deploy/entrypoint.sh /entrypoint.sh
RUN pip install --no-cache-dir . && chmod +x /entrypoint.sh
EXPOSE 8000
# entrypoint：等 PG 就绪 → bootstrap（建表/ES mapping/MinIO bucket/种身份）→ uvicorn
ENTRYPOINT ["/entrypoint.sh"]

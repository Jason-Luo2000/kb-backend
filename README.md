# kb-backend · 企业级知识库后端

「总结文档导航 + 向量召回」双路并行检索的知识库后端，带引用溯源、多租户 ACL、版本一致性、审计哈希链、摄入配额与 Prometheus 监控。完整设计见 `~/Developer/pi/KB-AGENT-PLAN.md`。

> **状态**：前期 MVP（T1–T8）+ 中期（T9–T17）**全部完成**。长期项（GDPR `/purge`、JWT/SSO、PG RLS、异步 saga、DeepDOC/OCR、Grafana 大盘）**待定**——用户目前不需要，详见 [项目状态](#项目状态)。

## 它做什么

- **双路召回**：路 A（总结文档导航 → 锚点回原文精读）+ 路 B（BM25 + KNN 向量），RRF 融合，每条命中带可溯源引用。
- **多租户 + ACL**：API-key → `(tenant,user)`，RBAC + kb_grant + clearance，三层纵深隔离（应用层 / ES 预过滤 / post-verify），跨租户零泄露。
- **多格式**：PDF / DOCX / PPTX / XLSX / HTML / MD，版式感知分块（标题边界、表格独立、position 沉淀）。
- **版本一致性**：transactional outbox + 原子 flip + 版本栅栏，PG 权威 / ES 派生；增量更新 + 幂等上传；GC 回收旧版本 + ES↔PG 对账自愈。
- **合规与可观测**：审计哈希链（防篡改 + 验证 + trust-anchor 锚）、摄入配额（docs/bytes）、Prometheus `/metrics` + upload→indexed SLO + `/readyz`。
- **SDK 1.0**：Python `kb-sdk` + TypeScript `kb_client`（幂等重试 + 结构化错误）。

## 架构

```
客户端 / pi 扩展 / SDK ──HTTP──► FastAPI 单体(kb-backend)
                                     │
        ┌──────────┬─────────────────┼────────────┬──────────┐
        ▼          ▼                 ▼            ▼          ▼
   PostgreSQL   ES 8.x           MinIO        Redis*     模型 API（多 provider）
   元数据/版本  BM25+KNN/版本    原文/对象    (预留)    LLM + embedding + rerank
        │
        └─ audit 哈希链 / 配额用量 / 查询日志 / 摄入计量
```
\* Redis 已声明依赖、当前未启用（异步 saga 留后期）。模型层为多 provider 适配器（OpenAI 兼容 / Anthropic / Gemini / 本地 等），登录「模型管理」配置；未配 embedding（或 provider 不可用）时自动退哈希伪向量（仅验证流程，生产配真 embedding）。

深入见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 快速开始

### A. Docker 一键部署（生产形态）

任意装了 Docker 的干净机器：

```bash
./deploy.sh          # 自动生成密钥 + 起 postgres/es/minio/redis/backend/web(nginx)
                     # 脚本会打印 KB_API_KEY（owner 登录 key）
```

打开 `http://<host>` → 用打印的 key 登录 →「模型管理」添加**任意渠道**的 embedding + LLM
（OpenAI / Anthropic / Gemini / 本地 vLLM·BGE / DeepSeek 等）→ 即可上传文档、问答。

- **API 直连**：`http://<host>:8000`（SDK / pi-ext）。
- **零预置 key**：`.env.example` 模板不含任何真实凭证 / provider key，全部由 `deploy.sh`
  现场生成（`KB_API_KEY` / `MODEL_SECRET` / PG·MinIO 凭证）或登录后自配。
- Linux 起 ES 前需 `sudo sysctl -w vm.max_map_count=262144`（脚本会提示；macOS Docker Desktop 一般已满足）。
- **重新部署**：保留 `.env`（含 `MODEL_SECRET`）→ `docker compose down && ./deploy.sh`；
  要搬现有数据另走 `pg_dump` + MinIO `mc mirror` + 重解析（PG 权威，ES 可重灌）。
- HTTPS 留给外层反代/负载均衡；本套为 80 明文。

### B. 本机内存模式（无 Docker / 无 GPU）

本机 PostgreSQL + Anaconda Python 3.12 即可跑通（检索用内存暴力 cosine + token 重叠）。

```bash
# 1. 建库（一次性）
psql postgres -c "create database kb"

# 2. venv + 依赖
cd ~/Developer/kb-backend
python3.12 -m venv .venv
.venv/bin/pip install -e .                  # 阿里源：-i https://mirrors.aliyun.com/pypi/simple/

# 3. 配 .env
cp .env.example .env
#   把 __GENERATE__ 占位（KB_API_KEY / MODEL_SECRET / POSTGRES_PASSWORD / MINIO_*）改成你自己的值
#   STORE_MODE=memory
#   DATABASE_URL=postgresql+psycopg:///kb   # 本机 PG socket
#   KB_BACKEND_URL=http://localhost:8001     # 8000 被占就用 8001
#   PATH_A_THETA=-1.0                        # 哈希伪向量时设；真 embedding 删此行
#   MIN_TOKENS_TO_SUMMARIZE=200
#   CHUNK_TOKEN_NUM=256
#   模型：启动后用 KB_API_KEY 登录「模型管理」加任意渠道 embedding + LLM（同 Docker）

# 4. 建表 + 起服务
.venv/bin/python -m app.bootstrap
.venv/bin/uvicorn app.main:app --port 8001

# 5. e2e（另开终端）
KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=<你 .env 里的 KB_API_KEY> \
  .venv/bin/python scripts/e2e_demo.py
```

**注意**：
- 未配 embedding（或 provider 429/超时）自动退**哈希伪向量**（无语义，仅验证流程），此时路 A 软门控须设 `PATH_A_THETA=-1`。生产请在「模型管理」配真 embedding。
- 内存模式重启清空索引（PG 元数据持久）；生产用 Docker 起真 ES/MinIO。`STORE_MODE=es`（默认）走真 ES/MinIO，业务代码不变。

## 客户端

```bash
pip install -e sdk/                     # Python kb-sdk 1.0
```
```python
from kb_sdk import KBClient
c = KBClient("http://localhost:8001", "<你的 KB_API_KEY>")
c.upload(kb_id, "doc.pdf")
hits = c.search("查询", knowledge_base_ids=[kb_id])
```

TypeScript：`pi-ext/kb_client.ts` 的 `makeKbClient()`（fetch + 重试 + 结构化错误）。详见 [docs/API.md](docs/API.md)。

## 前端控制台（web/）

Vite + React + TS + Ant Design 的 Web 控制台（SPA 调 HTTP API）：登录 / 知识库 / 文档上传 / 聊天式问答（带「查看原文」引用）/ 授权（admin）/ 运维面板（owner：配额·GC·对账·审计验链·锚）/ 监控（/readyz + 指标）。非 owner/admin 自动隐藏对应页。

```bash
# 后端先起（:8001），CORS 默认放行 :5173
cd web && npm install && npm run dev      # → http://localhost:5173，用你的 KB_API_KEY 登录
```

构建：`npm run build` → `web/dist`（可由后端 StaticFiles 挂载或 nginx 托管）。详见 [web/README.md](web/README.md)。

## pi 扩展接入

```bash
ln -s ~/Developer/kb-backend/pi-ext ~/.pi/agent/extensions/kb
KB_BACKEND_URL=http://localhost:8000 KB_USER_TOKEN=$KB_API_KEY pi
```
扩展暴露 4 个 LLM 工具（`kb_search` / `kb_read_anchor` / `kb_list` / `kb_cite`）+ `/kb` 命令 + 知识库助手人设。grant/upload/admin **不作为 LLM 工具**（防 prompt-injection 提权）。

## 目录

```
app/         后端：main/config/db/es/storage/adapters/ingest/retrieval/routers/middleware/audit/quota/metrics/indexing
sdk/kb_sdk/  Python SDK 1.0
pi-ext/      pi 扩展（TS）+ kb_client.ts
web/         前端控制台（Vite + React + AntD）
scripts/     e2e / 红队 / 各任务验收脚本（独立进程、需干净库）
tests/       pytest 单测（parser/chunker/sdk_retry/metrics）
docs/        ARCHITECTURE / API / OPERATIONS
```

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 双路召回、版本一致性、多租户 ACL、审计哈希链、配额、监控
- [docs/API.md](docs/API.md) — HTTP 端点参考（鉴权 / 错误码 / 示例）
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — 部署、GC/对账/配额/审计/监控、脚本、配置参考、排障

## 项目状态

**中期 T1–T17 全部完成**（均合 main）：

| 阶段 | 内容 |
|---|---|
| 前期 T1–T8 | MVP：双路召回 + RRF + 引用 + pi 接入 + Python SDK 雏形 |
| T9 | 多租户 + ACL（三层纵深，A5 跨租户零泄露） |
| T10 | 路 A 完整（simhash 稳定锚 / 重定位 / 软门控 / 超时软截止，A6） |
| T11 | 版本一致性（outbox + 原子 flip + 版本栅栏） |
| T12 | 增量更新 + 幂等上传（content_hash 去重，A7 复用 >60%） |
| T13 | 多格式解析 + 版式感知分块（OCR stub 留 flag） |
| T14 | 版本 GC + ES↔PG 对账（report+repair） |
| T15 | 审计哈希链 + 摄入计量/配额（413 KB_QUOTA_EXCEEDED） |
| T16 | 监控（Prometheus /metrics + upload→indexed SLO + /readyz，A8） |
| T17 | SDK 1.0（Python + TS，幂等重试 + 结构化错误） |

**长期项（待定，用户目前不需要）**：T20 GDPR `/purge`+deleted_at · T25 JWT/SSO · PG RLS · Redis Streams 异步 saga · DeepDOC/OCR/表格 TSR · 外部 trust anchor 发布 · SLO 告警 + Grafana 大盘。需要对应场景（接真人/过审/规模化/扫描件…）时再开。

## 测试

```bash
# 纯单测（无需 DB/ES/MinIO）
.venv/bin/pytest tests/ -q

# e2e（需服务在跑）
KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=<你的 KB_API_KEY> .venv/bin/python scripts/e2e_demo.py
KB_BACKEND_URL=http://localhost:8001 .venv/bin/python scripts/cross_tenant_test.py   # 跨租户红队（A5）

# 各任务验收脚本（独立进程、需干净库）
.venv/bin/python scripts/{version,increment,path_a,gc,reconcile,multiformat,audit,quota,metrics,sdk}_test.py
```

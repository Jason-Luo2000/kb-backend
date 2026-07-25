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
   PostgreSQL   ES 8.x           MinIO        Redis*     智谱 API
   元数据/版本  BM25+KNN/版本    原文/对象    (预留)    glm-5.2 + embedding-3
        │
        └─ audit 哈希链 / 配额用量 / 查询日志 / 摄入计量
```
\* Redis 已声明依赖、当前未启用（异步 saga 留后期）。模型层为适配器，默认智谱 API（免 GPU），余额不足自动退哈希伪向量（仅验证流程，生产换 BGE-M3）。

深入见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 快速开始

### A. Docker（生产形态）

```bash
cp .env.example .env            # 填 ZHIPU_API_KEY
docker compose up --build       # postgres / es / minio / redis / kb-backend
# 容器启动自动建 PG 表 + ES mapping + MinIO bucket + default 租户/owner/api_key
curl http://localhost:8000/healthz   # → {"ok":true}
```

> Linux 起 ES 前需 `sudo sysctl -w vm.max_map_count=262144`（macOS Docker Desktop 一般已满足）。

### B. 本机内存模式（无 Docker / 无 GPU）

本机 PostgreSQL + Anaconda Python 3.12 即可跑通（检索用内存暴力 cosine + token 重叠）。

```bash
# 1. 建库（一次性）
psql postgres -c "create database kb"

# 2. venv + 依赖
cd ~/Developer/kb-backend
/opt/anaconda3/bin/python3.12 -m venv .venv
.venv/bin/pip install -e .                  # 阿里源：-i https://mirrors.aliyun.com/pypi/simple/

# 3. 配 .env（关键项）
cp .env.example .env
#   STORE_MODE=memory
#   DATABASE_URL=postgresql+psycopg:///kb   # 本机 PG socket
#   ZHIPU_API_KEY=<你的智谱 key>
#   KB_BACKEND_URL=http://localhost:8001     # 8000 被占就用 8001
#   PATH_A_THETA=-1.0                        # 哈希伪向量时设；真 embedding 删此行
#   MIN_TOKENS_TO_SUMMARIZE=200
#   CHUNK_TOKEN_NUM=256

# 4. 建表 + 起服务
.venv/bin/python -m app.bootstrap
.venv/bin/uvicorn app.main:app --port 8001

# 5. e2e（另开终端）
KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=kb_dev_api_key \
  .venv/bin/python scripts/e2e_demo.py
```

**注意**：
- 智谱 embedding 单独计费，余额不足（429）自动退**哈希伪向量**（无语义，仅验证流程），此时路 A 软门控须设 `PATH_A_THETA=-1`。生产请充值或换本地 BGE-M3。
- 内存模式重启清空索引（PG 元数据持久）；生产用 Docker 起真 ES/MinIO。`STORE_MODE=es`（默认）走真 ES/MinIO，业务代码不变。

## 客户端

```bash
pip install -e sdk/                     # Python kb-sdk 1.0
```
```python
from kb_sdk import KBClient
c = KBClient("http://localhost:8001", "kb_dev_api_key")
c.upload(kb_id, "doc.pdf")
hits = c.search("查询", knowledge_base_ids=[kb_id])
```

TypeScript：`pi-ext/kb_client.ts` 的 `makeKbClient()`（fetch + 重试 + 结构化错误）。详见 [docs/API.md](docs/API.md)。

## 前端控制台（web/）

Vite + React + TS + Ant Design 的 Web 控制台（SPA 调 HTTP API）：登录 / 知识库 / 文档上传 / 聊天式问答（带「查看原文」引用）/ 授权（admin）/ 运维面板（owner：配额·GC·对账·审计验链·锚）/ 监控（/readyz + 指标）。非 owner/admin 自动隐藏对应页。

```bash
# 后端先起（:8001），CORS 默认放行 :5173
cd web && npm install && npm run dev      # → http://localhost:5173，用 kb_dev_api_key 登录
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
KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=kb_dev_api_key .venv/bin/python scripts/e2e_demo.py
KB_BACKEND_URL=http://localhost:8001 .venv/bin/python scripts/cross_tenant_test.py   # 跨租户红队（A5）

# 各任务验收脚本（独立进程、需干净库）
.venv/bin/python scripts/{version,increment,path_a,gc,reconcile,multiformat,audit,quota,metrics,sdk}_test.py
```

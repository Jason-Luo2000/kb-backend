# kb-backend · 企业级知识库后端（MVP 端到端最小闭环）

「总结文档导航 + 向量召回」双路并行的知识库后端，方案见 `~/Developer/pi/KB-AGENT-PLAN.md`。
本文档是 **前期 MVP + 中期 T9**：双路召回（路 A 简版 + 路 B）、RRF 融合、引用溯源、pi 扩展接入；T9 已叠加**多租户 + 用户↔KB 授权 + ACL**（单租户→多租户，零跨租户泄露）。

## 架构（MVP）

```
pi 扩展(5工具+人设) ──HTTP──► FastAPI 单体(kb-backend)
                                   │
        ┌──────────────┬───────────┼───────────┬──────────┐
        ▼              ▼           ▼           ▼          ▼
   PostgreSQL       ES 8.x      MinIO       Redis     智谱 API
   (元数据/版本)   (BM25+KNN)  (原文/总结)  (缓存)   (glm-5.2+embedding)
```

模型层为适配器：MVP 用智谱 API（免 GPU），后期可切 BGE-M3 / bge-reranker / DeepDoc。

## 前置（一次性）

Elasticsearch 需要宿主机内核参数（macOS Docker Desktop 一般已满足，Linux 需手动）：
```bash
# Linux only:
sudo sysctl -w vm.max_map_count=262144
```

## 启动

```bash
cd ~/Developer/kb-backend
cp .env.example .env        # 填入 ZHIPU_API_KEY
docker compose up --build   # 起 postgres/es/minio/redis/kb-backend
# kb-backend 容器启动时自动建 PG 表 + ES mapping + MinIO bucket
```

健康检查：`curl http://localhost:8000/healthz`

## 本机验证模式（无 Docker / 内存存储）

机器没有 Docker/brew/Java 时，可用「内存模式」跑通端到端（检索用内存暴力 cosine + token 重叠，生产换真 ES）。已在此模式验证：双路召回 A=4 B=5、RRF 融合、glm 总结、引用全链路通。

**前置**：本机 PostgreSQL（`psql postgres` 能连）+ Anaconda Python 3.12（系统自带 3.9 不支持 `X | None` 语法）。

```bash
# 1. 建库（一次性）
psql postgres -c "create database kb"

# 2. venv + 依赖（用 anaconda 的 3.12，清华源加速）
cd ~/Developer/kb-backend
/opt/anaconda3/bin/python3.12 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e .

# 3. 配 .env（关键项）
cp .env.example .env
#   至少设：
#     STORE_MODE=memory
#     DATABASE_URL=postgresql+psycopg:///kb          # 本机 PG socket 连接
#     ZHIPU_API_KEY=<你的智谱 key>
#     KB_BACKEND_URL=http://localhost:8001           # 8000 被占就用 8001
#     PATH_A_THETA=-1.0        # 验证用（哈希伪向量）；生产真 embedding 删此行用默认 0.2
#     MIN_TOKENS_TO_SUMMARIZE=200
#     CHUNK_TOKEN_NUM=256

# 4. 建表 + 起服务（pdfplumber import 慢，启动约 25s 才监听）
.venv/bin/python -m app.bootstrap
.venv/bin/uvicorn app.main:app --port 8001

# 5. e2e（另开终端）
KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=kb_dev_api_key \
  .venv/bin/python scripts/e2e_demo.py
```

**注意**：
- 智谱 embedding 需单独计费（glm-5.2 LLM 有额度，embedding 余额不足返回 429 / code 1113）。余额不足时 embedder 自动退回**哈希伪向量**（无语义，仅验证流程）；路 A 软门控此时须设 `PATH_A_THETA=-1` 才能召回。生产请充值或换本地 BGE-M3。
- 内存模式重启服务会清空索引（PG 元数据持久）；生产用 `docker compose` 起真 ES/MinIO。
- `app/es_memory.py` / `app/storage_memory.py` 是验证用替代实现，通过 `STORE_MODE` 开关，生产（`STORE_MODE=es`）走真 ES/MinIO，业务代码不变。

---

## 端到端 demo

```bash
pip install -e sdk/                 # 装 kb-sdk（或直接用 scripts/）
python scripts/e2e_demo.py          # 上传样例 → 摄取 → 双路检索 → 带引用答案

# 跨租户红队（T9 验收 A5，需服务在跑）：双租户互不可见 / read_anchor 不越权 / grant 可见性
KB_BACKEND_URL=http://localhost:8001 .venv/bin/python scripts/cross_tenant_test.py
```

## pi 接入

```bash
# 软链或复制扩展到 pi 的发现目录
ln -s ~/Developer/kb-backend/pi-ext ~/.pi/agent/extensions/kb
# 在任意终端：KB_BACKEND_URL=http://localhost:8000 KB_USER_TOKEN=$KB_API_KEY pi
```

## 目录

```
app/         后端：main / config / db / es / storage / adapters / ingest / retrieval / routers / middleware
pi-ext/      pi 扩展（TypeScript）
sdk/kb_sdk/  Python SDK
scripts/     e2e 验证与工具
tests/       测试
```

## 多租户与权限（T9）

中期第一阶段已叠加多租户 + ACL，**跨租户零泄露**（验收 A5）。三层纵深：

1. **应用层**：所有 file_id 解析收敛到 `tenant_id ∩ AuthzDecision.allowed_kb_ids`（[orchestrator._allowed_file_ids](app/retrieval/orchestrator.py)）。`/v1/read-anchor` 与 `/v1/search` 同级 ACL——任意 docId 不再能读未授权原文窗口（修复 MVP 的越权点）。
2. **ES 预过滤**：双路 filter 强制 `tenant_id_kwd` + `sensitivity<=clearance`（[path_a](app/retrieval/path_a.py)/[path_b](app/retrieval/path_b.py)）；摄取时 stamp tenant。
3. **post-verify**：RRF 融合后逐 chunk 回查租户，丢弃越权命中 + `SEC_VIOLATION` 审计（[guard.postverify](app/retrieval/guard.py)）。

**认证**：API-key → `(tenant_id,user_id)`（sha256 查 `kb_api_key`）。JWT/SSO 是后期 T25。bootstrap 幂等种 default 租户/owner/api_key → 现有 `KB_API_KEY`+`KB_USER_ID` 客户端无需改动即落入 default 租户。

**授权模型**（[app/authz.py](app/authz.py)，Python，接口 Cedar 形状，后期可 slot in cedar-py）：RBAC（租户角色 owner/admin/editor/viewer）+ `kb_grant`（用户↔KB 显式授权，含过期/撤销）+ `clearance>=sensitivity`（ABAC）。租户 owner/admin 见全部 kb；editor 见 team/tenant 可见 kb；viewer 仅显式 grant。

**端点**：`GET /v1/kbs`（带 role）、`POST /v1/kbs`（stamp tenant+owner）、`PUT/DELETE /v1/acl`（grant/revoke，仅 kb admin+，**高危非 LLM 工具**）。

> PG 行级安全（RLS）作为第四层纵深**缓做**——仅在非 superuser 应用角色下生效，需拆角色 + 解认证鸡生蛋，列为后期生产基础设施专项。A5 已由上述三层满足。

---

## 版本 GC + ES↔PG 对账（T14）

T11（版本化原子发布）+ T12（增量更新）每次摄取都留下一整代旧版本（旧 chunk 翻 `available=0` 行仍在、旧 summary/anchor 仅版本谓词隐藏、`kb_version` 每次+1、ES 旧 doc 翻 `available_int=0` 永不删、outbox published 行永不清）。旧版本已被版本栅栏 + flip 隐藏，**不影响可见性/正确性**——T14 负责**空间回收 + 漂移修复**（PG 权威、ES 派生）。

**GC / purge**（[app/indexing/gc.py](app/indexing/gc.py)）：按保留窗 `purge *_version < active - gc_retain_versions + 1`（默认 `gc_retain_versions=1`，回滚未实现只保当前）。整版本删除 anchor→summary_doc→chunk→version，ES 删除经 outbox `delete` 事件（与 PG 同事务写、commit 后 drain，旧 doc 已不可见故零检索影响）。审计内联同事务（不调 `audit()`）。并发双保险：advisory lock + `pending_count>0` 跳过；前置守卫断言四 active 指针相等。另含 `prune_outbox`（删 N 天前 published 行，保 pending/failed）。

**对账 reconcile**（[app/indexing/reconcile.py](app/indexing/reconcile.py)）：扫描分类 5 类漂移——`missing`（PG active 行、ES 无 → 原版本 re-embed 重发 `index`，**不调 ingest 防版本膨胀**）、`version_drift`（ES 版本≠active → 重发）、`avail_drift`（PG 可见、ES `available_int=0` → `set_available=1`）、`retired_leak`（ES 可见但属退役版本 → `set_available=0` 保到 GC）、`orphan`（PG 全无 → `delete`）。幂等。

**端点**（[app/routers/admin_ops.py](app/routers/admin_ops.py)，**仅租户 owner、高危非 LLM 工具**，`dryRun` 默认开）：
- `POST /v1/admin/gc` `{fileId?, dryRun}` — 旧版本 GC（`fileId` 省略=租户全量）
- `POST /v1/admin/reconcile` `{fileId?, dryRun, repair}` — ES↔PG 对账
- `POST /v1/admin/outbox/prune` `{retainDays?}` — outbox 修剪

**验证**：`scripts/gc_test.py`、`scripts/reconcile_test.py`（直接调模块、需干净库）。

> 范围边界：GDPR `/purge` + `deleted_at` 两阶段软删（被遗忘权）是 **T20/#31**，不在本期；MinIO 当前只存单 `{file_id}/v1/raw`（无按版本对象），按版本对象回收 N/A。

---

## 已实现 / 刻意简化（与方案的差异）

**T9 已补（多租户 + ACL）**：tenant 隔离（应用层 + ES + post-verify 三层）、`kb_grant`、AuthzEngine、`/v1/acl`、`read_anchor` 越权修复、审计落 tenant/user。PG RLS 第四层缓做（见上）。

**仍简化（后续任务）**：
- 解析用 pdfplumber（页码定位），后期换 DeepDoc（bbox）
- embedding 用智谱 embedding-3，后期换 BGE-M3（改适配器）
- 路 A 简版：锚点用 chunk_id（MVP 文档不变可接受），simhash 稳定锚 / 重定位 → **T10 已完成**
- 摄取同步处理 → T11 已完成（outbox + 原子 flip + 版本栅栏）；增量更新 / 幂等上传 → T12 已完成；版本 GC + ES↔PG 对账 → **T14 已完成**；多格式/分块器 → T13；审计哈希链 → T15；监控 → T16；SDK 1.0 → T17；JWT/SSO → T25
- rerank 可选（MVP 用 RRF 基线排序）

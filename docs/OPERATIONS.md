# 运维手册

## 部署

### Docker（生产形态）
```bash
cp .env.example .env            # 填 ZHIPU_API_KEY 等
docker compose up --build       # postgres/es/minio/redis/kb-backend
```
容器启动跑 `app.bootstrap`：建 PG 表 + ES mapping + MinIO bucket + default 租户/owner/api_key（幂等）。

### 本机内存模式（开发/无 Docker）
见 [README §快速开始 B](../README.md#b-本机内存模式无-docker--无-gpu)。要点：`STORE_MODE=memory`、本机 PG + Anaconda 3.12、`PATH_A_THETA=-1`（embedding 余额不足时）。

> 重建库：`psql postgres -c "drop database if exists kb"; psql postgres -c "create database kb"; .venv/bin/python -m app.bootstrap`

## Day-2 运维

所有运维端点 **owner-only**、`dryRun` 默认开。详见 [API.md §运维](./API.md#运维api-key--租户-ownerdryrun-默认开)。

### GC（回收旧版本空间）
T11/T12 每次摄取留一整代旧版本。GC 按保留窗清理（默认只保当前版本）：
```bash
curl -XPOST -H "Authorization: Bearer $KEY" -d '{"dryRun":true}'  $URL/v1/admin/gc        # 报告
curl -XPOST -H "Authorization: Bearer $KEY" -d '{"dryRun":false}' $URL/v1/admin/gc        # 执行（fileId 省略=租户全量）
```
前置守卫：四 active 指针须相等（否则报 desync 跳过）。ES 删经 outbox `delete` 事件，零检索影响。配 `GC_RETAIN_VERSIONS` 调保留窗。

### 对账（ES↔PG 漂移自愈）
```bash
curl -XPOST -H "Authorization: Bearer $KEY" -d '{"dryRun":true}'  $URL/v1/admin/reconcile  # 报告
curl -XPOST -H "Authorization: Bearer $KEY" -d '{"dryRun":false}' $URL/v1/admin/reconcile  # 修复
```
5 类漂移：missing/version_drift/avail_drift/retired_leak/orphan。幂等。

### outbox 修剪
```bash
curl -XPOST -H "Authorization: Bearer $KEY" -d '{"retainDays":7}' $URL/v1/admin/outbox/prune
```

### 审计哈希链
```bash
curl -H "Authorization: Bearer $KEY" $URL/v1/admin/audit/verify   # 验链（mismatch=字段篡改 / break=链路篡改 / gap=锁miss）
curl -XPOST -H "Authorization: Bearer $KEY" $URL/v1/admin/audit/anchor   # 快照 root_hash（外部发布待定）
```
`verified:false` 即检测到篡改。整段重写需外部 trust anchor 才能发现（待定）。

### 配额与用量
```bash
curl -H "Authorization: Bearer $KEY" $URL/v1/admin/quota   # {limits:{max_docs,max_bytes,period}, usage:{period,doc_count,bytes}}
```
超额上传 → 413 `KB_QUOTA_EXCEEDED`。调租户配额：改 `kb_quota` 行（或 config 缺省 `DEFAULT_QUOTA_DOCS`/`DEFAULT_QUOTA_BYTES`）。

### 监控
```bash
curl $URL/metrics | grep -E 'kb_ingest_duration|kb_http_requests_total|kb_path_a_completed'
curl $URL/readyz     # {db,es,minio}
```
SLO 告警规则 + Grafana 大盘属部署侧（待定）。

## 配置参考（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ZHIPU_API_KEY` | — | 智谱 key（LLM + embedding） |
| `STORE_MODE` | `es` | `es`（真 ES/MinIO）/ `memory`（内存替代，开发用） |
| `DATABASE_URL` | `postgresql+psycopg://kb:...@localhost:5432/kb` | 本机 socket 用 `postgresql+psycopg:///kb` |
| `ES_URL` / `REDIS_URL` / `MINIO_*` | localhost | 基础设施地址 |
| `KB_API_KEY` | `kb_dev_api_key` | bootstrap 种的 default api_key |
| `KB_USER_ID` | `u_demo` | default owner external_id |
| `CHUNK_TOKEN_NUM` / `CHUNK_OVERLAP` | 512 / 0.1 | 分块参数 |
| `MIN_TOKENS_TO_SUMMARIZE` | 1500 | 小于则跳过总结 |
| `PATH_A_THETA` | 0.2 | 路 A 软门控；哈希伪向量时设 `-1` |
| `PATH_A_RELOCATE_HAMMING` | 8 | 锚点重定位 simhash 阈值 |
| `MIN_CHANGED_RATIO` | 0.5 | 增量更新超此回退全量 |
| `GC_RETAIN_VERSIONS` | 1 | GC 保留版本数 |
| `OUTBOX_RETAIN_DAYS` | 7 | outbox 修剪保留期 |
| `OCR_ENABLED` | false | 扫描件 OCR（需 tesseract+poppler） |
| `DEFAULT_QUOTA_DOCS` / `DEFAULT_QUOTA_BYTES` | 1000 / 5GiB | 配额缺省（0=不限） |
| `CORS_ORIGINS` | `http://localhost:5173,...` | 前端跨域白名单（逗号分隔） |

完整列表见 [config.py](../app/config.py)。

## 脚本

均 `.venv/bin/python` 跑。`*` 标需服务在跑，其余独立进程（需干净库）。

| 脚本 | 说明 |
|---|---|
| `scripts/e2e_demo.py` * | 端到端：上传→摄取→双路检索→带引用答案 |
| `scripts/cross_tenant_test.py` * | 跨租户红队（A5：双租户互不可见/read_anchor 不越权/grant） |
| `scripts/sdk_test.py` * | kb-sdk 全客户端 e2e（含 admin） |
| `scripts/version_test.py` | T11 版本/原子 flip/drain barrier/版本栅栏 |
| `scripts/increment_test.py` | T12 增量复用/幂等（A7） |
| `scripts/path_a_test.py` | T10 锚点三态/重定位（A6，依赖 LLM） |
| `scripts/multiformat_test.py` | T13 多格式 e2e |
| `scripts/gc_test.py` | T14 GC/purge |
| `scripts/reconcile_test.py` | T14 对账 |
| `scripts/audit_test.py` | T15 哈希链+篡改检测+锚 |
| `scripts/quota_test.py` * | T15 低配额→413+计量 |
| `scripts/metrics_test.py` * | T16 /metrics+/readyz |

纯单测：`.venv/bin/pytest tests/ -q`（parser/chunker/sdk_retry/metrics，无需外部服务）。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| `bigint out of range` | simhash 64bit unsigned 超 PG BIGINT signed → 已转 signed（[simhash.to_signed](../app/retrieval/simhash.py)）；若再现检查是否漏转 |
| `UntranslatableCharacter` (JSONB) | 本地 PG SQL_ASCII → outbox payload 用 TEXT 已绕；新增 JSONB 列存中文需改 TEXT 或迁 UTF8 库 |
| embedding 429 / code 1113 | 智谱 embedding 余额不足 → 自动退哈希伪向量；设 `PATH_A_THETA=-1` 召回；生产换 BGE-M3 |
| path_a_test 报「无锚点」 | LLM 429（无 summary→无锚点），非代码回归；LLM 恢复后重跑 |
| upload 413 KB_QUOTA_EXCEEDED | 超 docs/bytes 配额；调 `kb_quota` 或 config 缺省 |
| `git push` 超时 | github 被代理拦（fake-ip 198.18.x.x）→ 修代理放行 github，重试 |
| `pip install` 失败 | 清华源偶发 403 → 换阿里源 `https://mirrors.aliyun.com/pypi/simple/` |
| audit `verified:false` (gap) | 多为 advisory lock miss 重启（非篡改）；看 `gaps` vs `recomputed_mismatches`/`prev_hash_breaks` 区分 |

## 升级既有库

schema.sql 用 `CREATE TABLE/INDEX IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（T14/T15 先例），`bootstrap._run_schema` 每次启动幂等执行 → **既有库重启即自动升级**，无需手动迁移。

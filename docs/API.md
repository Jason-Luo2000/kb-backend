# HTTP API 参考

Base URL：`http://localhost:8000`（Docker）或 `http://localhost:8001`（本机内存模式）。

## 鉴权

除 `/healthz`、`/readyz`、`/metrics` 外，所有端点需 API-key，二选一头：
- `Authorization: Bearer <token>`（推荐）
- `X-KB-API-Key: <token>`

token 经 sha256 查 `kb_api_key`（未撤销）→ `Principal(tenant_id, user_id)`。bootstrap 种 default 租户/owner/api_key（`KB_API_KEY=kb_dev_api_key`）。

## 错误约定

失败返回 `HTTPException(status, detail)`，body `{"detail": "KB_*"}`。kb-sdk 把 `detail` 映射为结构化异常（[errors.py](../sdk/kb_sdk/errors.py)）：

| detail / 状态 | 异常 | 含义 |
|---|---|---|
| 401 `KB_UNAUTHORIZED` | `KBUnauthorized` | token 缺失/无效/已撤销 |
| 403 `KB_FORBIDDEN_KB` | `KBForbidden` | 无该 kb 权限 |
| 403 `KB_FORBIDDEN_OWNER` | `KBForbidden` | 非 owner（admin 端点） |
| 404 `KB_DOC_NOT_FOUND` / `KB_KB_NOT_FOUND` / `KB_FILE_NOT_FOUND` | `KBNotFound` | 资源不存在（跨租户也 404，不泄漏存在性） |
| 404 `KB_ANCHOR_STALE` | `KBAnchorStale` | 锚点失效（重定位失败） |
| 409 `KB_VALIDATION` | `KBValidation` | kb 重名等校验冲突 |
| 413 `KB_QUOTA_EXCEEDED` | `KBQuotaExceeded` | 超 docs/bytes 配额（**不重试**） |
| 422 | `KBValidation` | 请求体校验错 |
| 429 | `KBRateLimited` | 限流（upload 30/min、search 120/min） |
| 5xx | `KBServerError` | 服务端错（SDK 仅对幂等动词重试） |

## 端点

### 健康与监控（无鉴权）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | `{"ok": true}` |
| GET | `/readyz` | `{db,es,minio}` 各 `"ok"`/`"fail"` |
| GET | `/metrics` | Prometheus 文本（RED + SLO + 业务 gauge） |

### 知识库与文档（API-key）

| 方法 | 路径 | 权限 | body / 返回 |
|---|---|---|---|
| GET | `/v1/me` | 任意 | `{tenant_id,user_id,is_owner,is_admin}`（前端验身份/门控） |
| GET | `/v1/kbs` | 任意 | `[{id,name,description,docCount,role,visibility}]` |
| POST | `/v1/kbs` | 任意 | `{name,description?,visibility?}` → `{id,name}`（重名 409） |
| GET | `/v1/kbs/{kb_id}/docs` | 任意（授权内） | `[{docId,title,status,pages,sizeBytes}]` |
| POST | `/v1/kbs/{kb_id}/docs` | editor+ | multipart `file` → `{docId,status,stats?}`（命中去重 `reused:true`；超额 413；30/min） |
| GET | `/v1/docs/{doc_id}` | 任意（授权内） | `{docId,title,status,pages}` |
| POST | `/v1/read-anchor` | 读授权 | `{docId,anchor,before?=2,after?=4}` → `{docId,anchor,text,page,version}` |
| POST | `/v1/search` | 任意 | `{query,knowledgeBaseIds?,topK?,mode?=hybrid}` → `{hits:[...],route_stats}`（120/min） |
| POST | `/v1/cite` | 任意 | `{answer,chunkIds}` → 带引用标注结果 |

**search 返回**：
```jsonc
{
  "hits": [{"docId","chunkId","page","snippet","score","path","citation":{chunkId,page}}],
  "route_stats": {"path_a","path_b","degraded","latency_ms","path_a_completed_rate","path_a_degraded_reason"}
}
```
`mode` ∈ `hybrid`（默认，双路）/ `summary`（仅路 A）/ `embedding`（仅路 B）。

### ACL（API-key + kb admin）

| 方法 | 路径 | body | 返回 |
|---|---|---|---|
| PUT | `/v1/acl` | `{kbId,userId,role?=viewer\|editor\|admin,expiresAt?}` | `{ok,kbId,userId,role}` |
| DELETE | `/v1/acl` | `{kbId,userId}` | `{ok:true}` |

> **高危、非 LLM 工具**（防 prompt-injection 提权），仅 admin UI/SDK 调用。`userId` 须是用户 UUID（非 external_id）。

### 运维（API-key + 租户 owner；`dryRun` 默认开）

| 方法 | 路径 | body | 返回 |
|---|---|---|---|
| POST | `/v1/admin/gc` | `{fileId?,dryRun?=true}` | purge 报告（`fileId` 省略=租户全量） |
| POST | `/v1/admin/reconcile` | `{fileId?,dryRun?,repair?=true}` | 5 类漂移检测/修复报告 |
| POST | `/v1/admin/outbox/prune` | `{retainDays?}` | `{deleted,retainDays}` |
| GET | `/v1/admin/audit/verify` | — | 哈希链验证（mismatch/break/gap） |
| POST | `/v1/admin/audit/anchor` | — | trust-anchor 快照（root_hash） |
| GET | `/v1/admin/quota` | — | `{limits,usage}` |

> GC/对账/prune/audit 是破坏性或重运维动作，**禁止作为 LLM 工具**。`dryRun=false` 才真执行；owner-only。

## 示例

```bash
# 上传
curl -XPOST -H "Authorization: Bearer kb_dev_api_key" \
  -F "file=@doc.pdf" http://localhost:8001/v1/kbs/$KB_ID/docs

# 检索
curl -XPOST -H "Authorization: Bearer kb_dev_api_key" -H "Content-Type: application/json" \
  -d '{"query":"双路召回","knowledgeBaseIds":["'$KB_ID'"]}' http://localhost:8001/v1/search

# GC（dry_run 报告）
curl -XPOST -H "Authorization: Bearer kb_dev_api_key" \
  -d '{"dryRun":true}' http://localhost:8001/v1/admin/gc

# 验审计链
curl -H "Authorization: Bearer kb_dev_api_key" http://localhost:8001/v1/admin/audit/verify
```

Python / TypeScript 客户端用法见 [README](../README.md#客户端) 与 [sdk/kb_sdk/](../sdk/kb_sdk/)、[pi-ext/kb_client.ts](../pi-ext/kb_client.ts)。

# 架构

kb-backend 是单进程 FastAPI 服务，PG 为权威存储、ES 为检索派生、MinIO 存原文对象、模型 provider 适配器提供 LLM+embedding+rerank（多渠道，登录「模型管理」配置）。完整设计动机与评审项见主方案 `~/Developer/pi/KB-AGENT-PLAN.md`，本文档讲**已实现的运行机制**。

## 总体数据流

```
上传 ─► docs.upload_doc ─► 配额预检 ─► MinIO 存原文 ─► PG 建文件行(status=parsing) + 计量
                                   │
                                   └─► pipeline.ingest_file（同步）：
                                         parse → chunk(naive_merge) → embed + summarize
                                         → PG 事务A(暂存 available=0 + outbox index 事件)
                                         → relay.drain（ES 落库）→ drain barrier
                                         → PG 事务B(flip 四 active 指针 + outbox set_available)
                                         → relay.drain（ES 翻可见）+ cost_log

检索 ─► search ─► orchestrator.retrieve：
        路A(path_a: 总结→锚点→原文窗口) ∥ 路B(path_b: BM25+KNN)
        → RRF 融合 → guard.postverify（版本栅栏+租户） → 带引用返回
```

## 1. 双路召回 + RRF 融合

- **路 A（总结文档导航）** [path_a.py](../app/retrieval/path_a.py)：先检索总结文档（section summary），命中后经**锚点**（[anchor.py](../app/retrieval/anchor.py)）回到原文同一位置读窗口精读，避免摘要失真进上下文。锚点三态：`valid`（源 chunk 在）/ `relocated`（simhash Hamming≤8 漂移重定位，T10 校准）/ `stale`。软门控（窗口逐 chunk max cos，`PATH_A_THETA`）+ 超时软截止 + 退化链。
- **路 B（向量+全文）** [path_b.py](../app/retrieval/path_b.py)：ES BM25 + KNN（dense_vector cosine）。
- **RRF 融合** [fusion.py](../app/retrieval/fusion.py)：`score = Σ weight/(k+rank)`，覆盖度降权。
- **post-verify** [guard.py](../app/retrieval/guard.py)：融合后逐 chunk 回查 PG（tenant 匹配 + file_id 授权 + `chunk_version == active`），丢弃越权/过期命中 + `SEC_VIOLATION` 审计。
- 检索延迟/路径统计落 `kb_query_log`（latency_ms / path_a/b_hits / degraded）。

## 2. 多租户 + ACL（三层纵深，A5 零泄露）

[authz.py](../app/authz.py) + [middleware/auth.py](../app/middleware/auth.py)

1. **应用层**：所有 file_id 解析收敛到 `tenant_id ∩ AuthzDecision.allowed_kb_ids`（[orchestrator._allowed_file_ids](../app/retrieval/orchestrator.py)）。`/v1/read-anchor` 与 `/v1/search` 同级 ACL——任意 docId 不能读未授权原文窗口。
2. **ES 预过滤**：双路 filter 强制 `tenant_id_kwd` + `sensitivity <= clearance`（path_a/path_b）。
3. **post-verify**：RRF 融合后逐 chunk 回查租户兜底。

**认证**：API-key → sha256 查 `kb_api_key` → `Principal(tenant_id, user_id, scopes)`，挂 `request.state`。头：`Authorization: Bearer <token>` 或 `X-KB-API-Key`。JWT/SSO 是后期 T25（待定）。

**授权模型**：RBAC（租户角色 owner/admin/editor/viewer）+ `kb_grant`（用户↔KB 显式授权，含过期/撤销）+ clearance>=sensitivity（ABAC）。owner/admin 见全部 kb；editor 见 team/tenant 可见 kb；viewer 仅显式 grant。接口 Cedar 形状，后期可 slot in cedar-py。

> PG 行级安全（RLS）作为第四层纵深**待定**（仅在非 superuser 应用角色下生效，需拆角色，改造面大）。A5 已由上述三层满足。

## 3. 版本一致性（PG 权威 / ES 派生）

[pipeline.py](../app/ingest/pipeline.py) + [indexing/relay.py](../app/indexing/relay.py)

- **四元组版本**：每次 ingest 产生 `doc/chunk/summary/anchor` 同版本号，`kb_file` 的四个 `active_*_version` 指针标记当前可见代。
- **stage→drain→flip 原子发布**：
  - 事务 A：新版本行 `available=0` 暂存 + outbox `index` 事件（与 PG 同事务）。
  - `relay.drain`：消费 outbox 事件落 ES（`index` 建 doc、`set_available` 翻可见）；幂等（确定性 id upsert），失败记 attempts 超 max 标 failed。
  - drain barrier：`pending_count==0` 才 flip（无半可见）。
  - 事务 B：单事务 flip 四 active 指针 + `available` 翻转 + outbox `set_available` 事件 → drain。
- **版本栅栏**：post-verify 只接受 `chunk_version == active` 的命中，防 relay 漂移泄漏旧版本。
- **outbox payload 用 TEXT**（非 JSONB）：本地 PG 是 SQL_ASCII，JSONB 解析含中文 payload 触发 `UntranslatableCharacter`；TEXT 存原始 JSON、relay 端 json.loads。
- **增量更新**（T12 [diff.py](../app/ingest/diff.py)）：`target>1` 且小改时未变 chunk 复用旧 embedding、全匹配 window 复用旧 summary（仅 fresh 重算）；超 `min_changed_ratio` 回退全量。复用项仍以新版本经 outbox 发布。
- **幂等上传**：`upload_doc` 按 `(tenant, content_hash)` 去重，命中返回已存 file_id 不重摄。

## 4. 版本 GC + ES↔PG 对账（T14）

[indexing/gc.py](../app/indexing/gc.py) + [indexing/reconcile.py](../app/indexing/reconcile.py)

- **GC/purge**：按保留窗 `purge *_version < active - gc_retain_versions + 1`（默认 1，回滚未实现只保当前）。整版本删 anchor→summary_doc→chunk→version；ES 删经 outbox `delete` 事件（同事务写、commit 后 drain；旧 doc 已不可见故零检索影响）。审计内联同事务；advisory lock + `pending_count>0` 双保险；前置守卫断言四 active 相等。另含 `prune_outbox`。
- **对账 reconcile**（report + repair）：5 类漂移——`missing`（PG 有 ES 无 → 原版本 re-embed 重发 `index`，**不调 ingest 防版本膨胀**）/ `version_drift` / `avail_drift`（set_available=1）/ `retired_leak`（set_available=0）/ `orphan`（delete）。幂等。

## 5. 多格式解析 + 版式分块（T13）

[adapters/parser.py](../app/adapters/parser.py) + [ingest/chunker.py](../app/ingest/chunker.py)

- **Parser Registry**：mime → 扩展名 → 默认(MD/TXT)。`Block{page,text,section_path,block_type,bbox,level}`。支持 PDF（pdfplumber + find_tables + 页级 bbox）/ DOCX / PPTX / XLSX（200k 单元上限）/ HTML / MD。
- **naive_merge**：`table/figure`=屏障（独立 chunk + `skip_summary`）；`title`=边界（flush+作新段种子）；prose 累加到 token 上限 + overlap carry；超 size 滑窗。沉淀 `position=[{page,l,t,r,b}]`（仅 PDF 真 bbox，Office→NULL）。
- **MD 字节稳定**：标题+正文 `\n` 重连与旧实现逐字一致 → 未变 MD 文件 content_hash 不变 → T12 增量 100% 复用。
- **OCR stub**：`OCR_ENABLED` 默认关，lazy import pytesseract，本地无 tesseract → warn+丢页；real OCR 走 Dockerfile 后续。

## 6. 审计哈希链 + 配额/计量（T15）

[audit.py](../app/audit.py) + [quota.py](../app/quota.py)

- **哈希链**：`kb_audit_log` 加 `prev_hash/row_hash`(BYTEA)。统一 `append_audit(conn,...)`（audit()/gc/reconcile 全走），per-tenant advisory lock 串行写（保 audit() 不阻塞；锁 miss → prev_hash=NULL best-effort 重启）。canonical 单一 helper（insert/verify 共用防漂移）：`row_hash = sha256(prev_hex + "|" + json(payload, sort_keys))`，排除 created_at。
- **verify_audit_chain**：重算检测字段篡改（mismatch）+ 链路篡改（break），gap 计锁 miss。
- **anchor_audit**：写 `kb_audit_anchor` 快照（root_hash=链尾 row_hash，累积摘要）。
  > **红队修正**：哈希链只是相对完整性，整段重写测不出——必须锚定外部 trust anchor（WORM/签名/Merkle）。`published` bool 是 seam，外部发布待定。
- **配额**：`kb_quota`(max_docs/max_bytes/monthly) + `kb_usage` + `kb_file.size_bytes`。上传前预检（去重新文件后、MinIO 前），超额 → 413 `KB_QUOTA_EXCEEDED`（非 429，SDK 不重试）。新摄同事务 `meter_ingest`（reused 不计数）。
- **摄入计量**：`kb_ingest_cost_log` 每 ingest 记 chunks/tokens/model（cost 计算 defer）。

## 7. 监控（T16）

[metrics.py](../app/metrics.py) + [main.py](../app/main.py)

- **HTTP RED 中间件**：`kb_http_requests_total{method,endpoint,status}` + `kb_request_duration_seconds`。endpoint 用路由模板避免 UUID 爆基数。
- **SLO 直方图**：`kb_ingest_duration_seconds{tenant,outcome}`（包 ingest_file，A8 p95<5min；失败也 observe）、`kb_path_a_completed_rate`（查询侧）。
- **业务 Gauge**（`collect_business_metrics`，best-effort 短窗 SQL）：retrieval p95 / ingest 计数·tokens / quota 用量 / SEC_VIOLATION 计数（按 tenant）。
- **`/metrics`**（无鉴权内部采集）+ **`/readyz`**（DB/ES/MinIO 探活）。
  > 昂贵项（audit verify / reconcile drift）走 admin 端点按需，不进 /metrics。SLO 告警规则 + Grafana 大盘属部署侧（待定）。

## 关键评审项对照

| # | 评审项 | 落地 |
|---|---|---|
| #11 | ES↔PG 双写不一致 | transactional outbox + 版本栅栏 + 对账 |
| #15 | 重试非幂等、超时放大 | SDK 仅幂等重试 + 分级超时（T17） |
| #22/#28 | 可见性/原子发布 | 四 active 指针 flip + 版本栅栏（T11） |
| #23/#26 | 增量/幂等上传 | content_hash 去重 + diff 复用（T12） |
| #27 | 无 GC/对账 | gc/purge + ES↔PG 对账（T14） |
| #29 | 无摄入计量/配额 | ingest_cost_log + 上传预检（T15） |
| #30 | 无摄入监控 | Prometheus + upload→indexed SLO（T16） |

> 完整评审项表见主方案 §A。

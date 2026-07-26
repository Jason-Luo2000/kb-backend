-- 多租户 schema（T9：方案 §5 的子集）。
-- 单租户→多租户：全表加 tenant_id，新增 kb_tenant/kb_user/kb_user_tenant/kb_grant/kb_api_key；
-- content_hash 去重改租户边界；read_anchor 越权由应用层 + post-verify 兜底（PG RLS 见 Phase 7）。
-- 刻意不含 T10(simhash)/T11(outbox/kb_version)/T15(哈希链) 的列与表。

-- ============ 租户与用户 ============
CREATE TABLE IF NOT EXISTS kb_tenant (
  id UUID PRIMARY KEY,
  name VARCHAR(128) NOT NULL UNIQUE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_user (
  id UUID PRIMARY KEY,
  external_id VARCHAR(128) NOT NULL UNIQUE,        -- 对接 OIDC sub / SAML nameid（SSO 见 T25）
  name VARCHAR(128),                               -- 显示名（成员管理用）
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_user_tenant (
  user_id UUID NOT NULL REFERENCES kb_user(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  role VARCHAR(24) NOT NULL DEFAULT 'viewer',       -- owner|admin|editor|viewer（RBAC 租户内粗粒度）
  department VARCHAR(64),                           -- 部门标签（成员管理筛选用；非组级 ACL）
  group_name VARCHAR(64),                           -- 小组标签（部门下的子分组；筛选/批量授权用）
  PRIMARY KEY (user_id, tenant_id)
);
ALTER TABLE kb_user ADD COLUMN IF NOT EXISTS name VARCHAR(128);          -- 已存库升级
ALTER TABLE kb_user_tenant ADD COLUMN IF NOT EXISTS department VARCHAR(64);
ALTER TABLE kb_user_tenant ADD COLUMN IF NOT EXISTS group_name VARCHAR(64);

CREATE TABLE IF NOT EXISTS kb_api_key (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES kb_user(id) ON DELETE CASCADE,
  key_hash CHAR(64) NOT NULL UNIQUE,                -- sha256(token)
  scopes JSONB DEFAULT '["*"]'::jsonb,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_apikey_hash ON kb_api_key(key_hash) WHERE revoked_at IS NULL;

-- ============ 知识库与授权（方案需求#1：用户↔KB 多对多）============
CREATE TABLE IF NOT EXISTS kb_kb (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  name VARCHAR(128) NOT NULL,
  description TEXT,
  visibility VARCHAR(16) NOT NULL DEFAULT 'team',   -- me|team|tenant
  owner_id UUID REFERENCES kb_user(id),
  parser_config JSONB DEFAULT '{"chunk_token_num":512,"overlap":0.1}'::jsonb,
  summary_config JSONB DEFAULT '{"mode":"summary","window_tokens":8000,"min_tokens":1500}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS kb_grant (                -- 评审#21：统一授权表
  grant_id UUID PRIMARY KEY,
  kb_id UUID NOT NULL REFERENCES kb_kb(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES kb_user(id) ON DELETE CASCADE,
  role VARCHAR(24) NOT NULL,                         -- viewer|editor|admin
  granted_by UUID REFERENCES kb_user(id),
  granted_at TIMESTAMPTZ DEFAULT now(),
  expires_at TIMESTAMPTZ,
  source VARCHAR(24) DEFAULT 'explicit',             -- explicit|inherited|sso_group
  revoked_at TIMESTAMPTZ,
  UNIQUE (kb_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_grant_user ON kb_grant(user_id);

-- ============ 文件（一等公民）与多库归属 ============
CREATE TABLE IF NOT EXISTS kb_file (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  storage_key VARCHAR(512) NOT NULL,
  name VARCHAR(512),
  content_hash CHAR(64) NOT NULL,                    -- sha256
  mime VARCHAR(128),
  size_bytes BIGINT,                                  -- T15：摄入配额（storage bytes）
  page_count INT,
  parser_type VARCHAR(32) DEFAULT 'naive',
  summary_enabled SMALLINT DEFAULT 1,
  active_doc_version INT DEFAULT 1,                  -- 评审#22/#28：四维独立 active 指针（MVP 已有，保持）
  active_chunk_version INT DEFAULT 1,
  active_summary_version INT DEFAULT 1,
  active_anchor_version INT DEFAULT 1,
  status VARCHAR(16) DEFAULT 'pending',
  owner_user_id UUID,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (tenant_id, content_hash)                   -- 评审#18：去重在租户边界内
);

CREATE TABLE IF NOT EXISTS kb_file_kb (
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  kb_id UUID NOT NULL REFERENCES kb_kb(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  added_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (file_id, kb_id)
);

-- ============ 分块（per-file 共享）============
CREATE TABLE IF NOT EXISTS kb_chunk (
  id UUID PRIMARY KEY,                               -- 确定性 uuid_v5（见 pipeline._chunk_id）
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,  -- 冗余，便于 RLS/post-verify
  doc_version INT NOT NULL DEFAULT 1,
  chunk_order INT NOT NULL,
  content TEXT NOT NULL,
  content_ltks TEXT,
  section_path VARCHAR(512),
  page_num INT,
  position JSONB,
  chunk_version INT NOT NULL DEFAULT 1,
  content_hash CHAR(64),
  simhash BIGINT,                                    -- T10：64bit simhash，锚点重定位用
  sensitivity_level SMALLINT DEFAULT 0,             -- clearance ABAC 载体（T9 全 0，字段就位）
  available SMALLINT DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_chunk_file ON kb_chunk(file_id, chunk_order);
CREATE INDEX IF NOT EXISTS idx_chunk_tenant ON kb_chunk(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_file_version ON kb_chunk(file_id, chunk_version);  -- T14：GC 按版本清

-- ============ 总结文档（路A 检索层）============
CREATE TABLE IF NOT EXISTS kb_summary_doc (
  id UUID PRIMARY KEY,
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  summary_type VARCHAR(16) NOT NULL,
  heading_path TEXT,
  content_md TEXT NOT NULL,
  summary_text TEXT,
  content_fingerprint CHAR(16),                      -- T10：summary 文本 simhash(hex16)，summary 身份用
  source_chunk_ids UUID[] NOT NULL,                  -- 锚点指回原文
  coverage_ratio FLOAT,
  summary_version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_summary_file ON kb_summary_doc(file_id, summary_type);
CREATE INDEX IF NOT EXISTS idx_summary_file_version ON kb_summary_doc(file_id, summary_version);  -- T14：GC 按版本清

CREATE TABLE IF NOT EXISTS kb_anchor (
  id UUID PRIMARY KEY,
  summary_doc_id UUID NOT NULL REFERENCES kb_summary_doc(id) ON DELETE CASCADE,
  file_id UUID NOT NULL,
  section_path VARCHAR(512) NOT NULL,
  target_chunk_id UUID,                              -- 运行期缓存（T10 可被重定位改写）
  target_content_hash CHAR(64),                      -- T10：目标 chunk 文本 sha256（精确校验）
  fingerprint CHAR(16),                              -- T10：目标 chunk 文本 simhash(hex16)，重定位用
  validity VARCHAR(16) DEFAULT 'valid',              -- T10：valid|stale|relocated
  relocated_from_chunk_id UUID,                      -- T10：重定位前的原 chunk_id（可审计）
  verified_against_chunks_version INT,               -- T10：重定位时对齐的 chunk_version
  anchor_version INT NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_anchor_summary ON kb_anchor(summary_doc_id);
CREATE INDEX IF NOT EXISTS idx_anchor_file_version ON kb_anchor(file_id, anchor_version);  -- T14：GC 按版本清（无 available 列，靠版本谓词）

-- ============ 检索审计与引用溯源 ============
CREATE TABLE IF NOT EXISTS kb_query_log (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID,
  user_id UUID,
  query_norm TEXT,
  file_ids UUID[],
  path_a_hits INT,
  path_b_hits INT,
  path_degraded VARCHAR(16),
  rerank_used BOOLEAN,
  latency_ms INT,
  tokens_in INT,
  tokens_out INT,
  answer_md TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_querylog_tenant_time ON kb_query_log(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS kb_audit_log (            -- append-only（哈希链/trust anchor 见 T15）
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID,
  user_id UUID,
  action VARCHAR(32) NOT NULL,
  kb_ids UUID[],
  query_text TEXT,
  hit_chunk_ids UUID[],
  result VARCHAR(16),
  request_id VARCHAR(64),
  ip INET,
  user_agent TEXT,
  detail JSONB,                                       -- T14：GC/对账的结构化明细（计数/dry_run/阈值），ASCII 值无 SQL_ASCII 问题
  prev_hash BYTEA,                                    -- T15：哈希链前一节 row_hash（NULL=链头/锁未取 best-effort 重启）
  row_hash BYTEA,                                     -- T15：本行 canonical 字段 sha256
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON kb_audit_log(created_at DESC);
ALTER TABLE kb_audit_log ADD COLUMN IF NOT EXISTS detail JSONB;  -- T14：已存在的库升级（CREATE IF NOT EXISTS 不会补列）
ALTER TABLE kb_audit_log ADD COLUMN IF NOT EXISTS prev_hash BYTEA;  -- T15 升级
ALTER TABLE kb_audit_log ADD COLUMN IF NOT EXISTS row_hash BYTEA;   -- T15 升级
CREATE INDEX IF NOT EXISTS idx_audit_chain ON kb_audit_log(tenant_id, id DESC) WHERE row_hash IS NOT NULL;  -- T15：找链尾

-- ============ 版本与一致性（T11）============
CREATE TABLE IF NOT EXISTS kb_version (                -- 评审#25/#6：四元组绑定（一次摄取 doc/chunk/summary/anchor 同进同退）
  id UUID PRIMARY KEY,
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  doc_version INT NOT NULL,
  chunk_version INT NOT NULL,
  summary_version INT NOT NULL,
  anchor_version INT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_version_file ON kb_version(file_id, doc_version DESC);

CREATE TABLE IF NOT EXISTS kb_outbox (                 -- 评审#11：transactional outbox（PG 权威、ES 派生）
  id BIGSERIAL PRIMARY KEY,
  aggregate_id UUID NOT NULL,                          -- file_id
  event_type VARCHAR(24) NOT NULL,                     -- index | set_available | delete（T14）
  payload TEXT NOT NULL,                               -- JSON 文本（TEXT 兼容 SQL_ASCII 服务端；relay 端 json.loads）
  status VARCHAR(16) DEFAULT 'pending',                -- pending | published | failed
  attempts INT DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  published_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON kb_outbox(aggregate_id, created_at) WHERE published_at IS NULL;

-- ============ 审计哈希链 trust anchor + 摄入计量/配额（T15，review #29）============
ALTER TABLE kb_file ADD COLUMN IF NOT EXISTS size_bytes BIGINT;                       -- T15：已存库升级
CREATE INDEX IF NOT EXISTS idx_file_tenant_created ON kb_file(tenant_id, created_at); -- T15：用量聚合回退

CREATE TABLE IF NOT EXISTS kb_audit_anchor (          -- T15：链快照（trust anchor seam；外部 WORM/签名发布 defer）
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID,                                     -- NULL = 全局桶
  head_id BIGINT NOT NULL,
  tail_id BIGINT NOT NULL,                            -- 链尾（cumulative digest 已承诺全链）
  row_count INT NOT NULL,
  root_hash BYTEA NOT NULL,                           -- = 链尾 row_hash
  anchored_at TIMESTAMPTZ DEFAULT now(),
  published BOOLEAN DEFAULT false                     -- 外部发布状态（stub）
);
CREATE INDEX IF NOT EXISTS idx_anchor_tenant_time ON kb_audit_anchor(tenant_id, anchored_at DESC);

CREATE TABLE IF NOT EXISTS kb_quota (                 -- T15：租户配额上限（0 = 不限）
  tenant_id UUID PRIMARY KEY REFERENCES kb_tenant(id) ON DELETE CASCADE,
  max_docs INT NOT NULL,
  max_bytes BIGINT NOT NULL,
  period VARCHAR(16) NOT NULL DEFAULT 'monthly'
);

CREATE TABLE IF NOT EXISTS kb_usage (                 -- T15：租户月度用量计数（预检 + 计量）
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  period VARCHAR(7) NOT NULL,                         -- 'YYYY-MM'
  doc_count INT NOT NULL DEFAULT 0,
  bytes BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, period)
);

CREATE TABLE IF NOT EXISTS kb_ingest_cost_log (       -- T15：摄入计量（cost 计算 defer 占位）
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID,
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  chunks INT NOT NULL,
  tokens INT NOT NULL,                                -- cl100k 近似（embedding 输入成本估算）
  model VARCHAR(64),
  cost NUMERIC(10,4) DEFAULT 0,                       -- defer：pricing/cost 计算后续
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_costlog_tenant_time ON kb_ingest_cost_log(tenant_id, created_at DESC);

-- ============ 数据看板：chat 结果埋点（analytics）============
-- /v1/chat 生成后写一行：outcome=answered|no_result|error；供看板统计回答质量/模型调用。
CREATE TABLE IF NOT EXISTS kb_chat_log (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID,
  user_id UUID,
  query TEXT,
  answer TEXT,                        -- 生成的答案（管理员查看用户问答记录用）
  model VARCHAR(128),
  outcome VARCHAR(16),                -- answered | no_result | error
  hits INT,
  latency_ms INT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chatlog_tenant_time ON kb_chat_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chatlog_user_time ON kb_chat_log(user_id, created_at DESC);
ALTER TABLE kb_chat_log ADD COLUMN IF NOT EXISTS answer TEXT;  -- 已存库升级

-- ============ 问答会话（用户级，多会话 + 持久化历史）============
CREATE TABLE IF NOT EXISTS kb_conversation (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES kb_user(id) ON DELETE CASCADE,
  title VARCHAR(256),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conv_user_time ON kb_conversation(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS kb_message (
  id BIGSERIAL PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES kb_conversation(id) ON DELETE CASCADE,
  role VARCHAR(16) NOT NULL,                -- user | assistant
  content TEXT,
  meta JSONB,                               -- references/route_stats/model/error（assistant 用）
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON kb_message(conversation_id, id);

-- ============ 模型 provider 注册表（M：多 provider 模型配置）============
-- tenant_id NULL = 系统内置（env 种子，bootstrap 启动种）；租户行覆盖系统行。
-- kind: llm | embedding | rerank；provider_type: openai | anthropic | zhipu | local | gemini
-- api_key_enc: Fernet 加密（app/crypto）；读取时解密、出 API 时脱敏。
-- 每 (tenant_id, kind) 至多一条 is_default=1（应用层维护）。
CREATE TABLE IF NOT EXISTS kb_model_config (
  id UUID PRIMARY KEY,
  tenant_id UUID REFERENCES kb_tenant(id) ON DELETE CASCADE,
  name VARCHAR(128) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  provider_type VARCHAR(24) NOT NULL,
  base_url VARCHAR(512),
  api_key_enc TEXT,
  model_name VARCHAR(128) NOT NULL,
  dim INT,
  max_tokens INT,                                  -- LLM 最大输出 token（模型级；None→default_llm_max_tokens）
  is_default SMALLINT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_model_tenant_kind ON kb_model_config(tenant_id, kind);
CREATE INDEX IF NOT EXISTS idx_model_default ON kb_model_config(kind) WHERE is_default = 1 AND tenant_id IS NULL;
ALTER TABLE kb_model_config ADD COLUMN IF NOT EXISTS max_tokens INT;  -- 已存库升级

-- ============ C：分块配置（per-file）============
-- parser_config 存生效的分块配置 {method,chunk_token_num,overlap,delimiter,layout_recognize}。
-- 解析顺序：file.parser_config → 所在 KB 的 kb_kb.parser_config → env 默认。
ALTER TABLE kb_file ADD COLUMN IF NOT EXISTS parser_config JSONB;



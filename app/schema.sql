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
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_user_tenant (
  user_id UUID NOT NULL REFERENCES kb_user(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  role VARCHAR(24) NOT NULL DEFAULT 'viewer',       -- owner|admin|editor|viewer（RBAC 租户内粗粒度）
  PRIMARY KEY (user_id, tenant_id)
);

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
  sensitivity_level SMALLINT DEFAULT 0,             -- clearance ABAC 载体（T9 全 0，字段就位）
  available SMALLINT DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_chunk_file ON kb_chunk(file_id, chunk_order);
CREATE INDEX IF NOT EXISTS idx_chunk_tenant ON kb_chunk(tenant_id);

-- ============ 总结文档（路A 检索层）============
CREATE TABLE IF NOT EXISTS kb_summary_doc (
  id UUID PRIMARY KEY,
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES kb_tenant(id) ON DELETE CASCADE,
  summary_type VARCHAR(16) NOT NULL,
  heading_path TEXT,
  content_md TEXT NOT NULL,
  summary_text TEXT,
  source_chunk_ids UUID[] NOT NULL,                  -- 锚点指回原文
  coverage_ratio FLOAT,
  summary_version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_summary_file ON kb_summary_doc(file_id, summary_type);

CREATE TABLE IF NOT EXISTS kb_anchor (
  id UUID PRIMARY KEY,
  summary_doc_id UUID NOT NULL REFERENCES kb_summary_doc(id) ON DELETE CASCADE,
  file_id UUID NOT NULL,
  section_path VARCHAR(512) NOT NULL,
  target_chunk_id UUID,                              -- MVP 锚点=chunk_id（稳定锚重定位见 T10）
  fingerprint CHAR(16),
  validity VARCHAR(16) DEFAULT 'valid',
  anchor_version INT NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_anchor_summary ON kb_anchor(summary_doc_id);

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
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON kb_audit_log(created_at DESC);

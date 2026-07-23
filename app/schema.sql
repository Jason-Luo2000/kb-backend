-- MVP 单租户简化 schema（方案 §5 的子集；去掉 tenant_id/RLS/kb_grant，保留四元 active 指针与确定性 chunk_id）
CREATE TABLE IF NOT EXISTS kb_kb (
  id UUID PRIMARY KEY,
  name VARCHAR(128) NOT NULL UNIQUE,
  description TEXT,
  parser_config JSONB DEFAULT '{"chunk_token_num":512,"overlap":0.1}',
  summary_config JSONB DEFAULT '{"mode":"summary","window_tokens":8000,"min_tokens":1500}',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_file (
  id UUID PRIMARY KEY,
  storage_key VARCHAR(512) NOT NULL,
  name VARCHAR(512),
  content_hash CHAR(64) NOT NULL UNIQUE,
  mime VARCHAR(128),
  page_count INT,
  parser_type VARCHAR(32) DEFAULT 'naive',
  summary_enabled SMALLINT DEFAULT 1,
  active_doc_version INT DEFAULT 1,
  active_chunk_version INT DEFAULT 1,
  active_summary_version INT DEFAULT 1,
  active_anchor_version INT DEFAULT 1,
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_file_kb (
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  kb_id UUID NOT NULL REFERENCES kb_kb(id) ON DELETE CASCADE,
  added_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (file_id, kb_id)
);

CREATE TABLE IF NOT EXISTS kb_chunk (
  id UUID PRIMARY KEY,
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  doc_version INT NOT NULL DEFAULT 1,
  chunk_order INT NOT NULL,
  content TEXT NOT NULL,
  content_ltks TEXT,
  section_path VARCHAR(512),
  page_num INT,
  position JSONB,
  chunk_version INT NOT NULL DEFAULT 1,
  content_hash CHAR(64),
  available SMALLINT DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_chunk_file ON kb_chunk(file_id, chunk_order);

CREATE TABLE IF NOT EXISTS kb_summary_doc (
  id UUID PRIMARY KEY,
  file_id UUID NOT NULL REFERENCES kb_file(id) ON DELETE CASCADE,
  summary_type VARCHAR(16) NOT NULL,
  heading_path TEXT,
  content_md TEXT NOT NULL,
  summary_text TEXT,
  source_chunk_ids UUID[] NOT NULL,
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
  target_chunk_id UUID,
  fingerprint CHAR(16),
  validity VARCHAR(16) DEFAULT 'valid',
  anchor_version INT NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_anchor_summary ON kb_anchor(summary_doc_id);

CREATE TABLE IF NOT EXISTS kb_query_log (
  id BIGSERIAL PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS kb_audit_log (
  id BIGSERIAL PRIMARY KEY,
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

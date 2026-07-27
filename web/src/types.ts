export interface Me {
  tenant_id: string;
  user_id: string;
  is_owner: boolean;
  is_admin: boolean;
}

export interface KB {
  id: string;
  name: string;
  description?: string;
  docCount: number;
  role: string;
  visibility: string;
  parserConfig?: ParserConfig | null;
}

export interface Doc {
  docId: string;
  title: string;
  status: string;
  pages: number | null;
  sizeBytes: number | null;
  parserType?: string;
  parserConfig?: ParserConfig | null;
}

// ---- 分块配置（C）----
export interface ParserConfig {
  method?: string;
  chunk_token_num?: number;
  overlap?: number;
  delimiter?: string;
  layout_recognize?: string;
}
export interface ParserMethod {
  name: string;
  label: string;
  domain: boolean;
}

// ---- 个人文件库（F）----
export interface DriveFile {
  fileId: string;
  name: string;
  status: string;
  sizeBytes: number | null;
  parserType?: string;
  kbCount: number;
  createdAt?: string | null;
}

export interface Citation {
  chunkId: string;
  page: number;
}

export interface Hit {
  docId: string;
  chunkId: string;
  page: number;
  snippet: string;
  score: number;
  path: string;
  citation: Citation;
}

export interface RouteStats {
  path_a: number;
  path_b: number;
  degraded: string;
  rerank_used?: boolean;
  latency_ms: number;
  path_a_completed_rate?: number | null;
  path_a_degraded_reason?: string;
}

export interface SearchResult {
  hits: Hit[];
  route_stats: RouteStats;
}

// ---- RAG 问答（/v1/chat）----
export interface ChatModel {
  id: string;
  name: string;
  modelName: string;
  isDefault: boolean;
}
export interface Reference {
  index: number;
  docId: string;
  chunkId: string;
  page: number | null;
  snippet: string;
}
export interface ChatResult {
  answer: string | null;
  references: Reference[];
  hits: Hit[];
  route_stats: RouteStats;
  model: string | null;
  error: string | null;
}

// ---- 成员管理（/v1/admin/users）----
export interface Member {
  userId: string;
  externalId: string;
  name?: string | null;
  role: string;
  department?: string | null;
  groupName?: string | null;
  kbCount: number;
  createdAt?: string | null;
}
export interface UserKb {
  kbId: string;
  name: string;
  visibility: string;
  role: string;
  source: string; // 授权 | 角色/可见性 | 授权(提升)
  canRevoke: boolean;
}

// ---- 数据看板（analytics）----
export interface AnalyticsOverview {
  days: number;
  total_qa: number;
  chats: number;
  answered: number;
  no_result: number;
  error: number;
  success_rate: number | null;
  active_users: number;
  uploads: number;
  rerank_uses: number;
}
export interface TopQuery { query: string; count: number; }
export interface UserUsage { userId: string; externalId: string; queries: number; chats: number; uploads: number; }
export interface ModelUsage { model: string; type: "llm" | "embedding"; calls: number; }
export interface ChatRecord {
  id: number;
  query: string;
  answer: string | null;
  model: string | null;
  outcome: string;
  hits: number;
  latencyMs: number;
  createdAt: string | null;
}

// ---- 问答会话（用户级历史）----
export interface Conversation {
  id: string;
  title: string;
  updatedAt: string | null;
  preview: string;
}
export interface ConvMessage {
  role: "user" | "assistant";
  content: string | null;
  meta: { references?: Reference[]; route_stats?: RouteStats; model?: string | null; error?: string | null } | null;
  createdAt: string | null;
}

export interface ReadAnchorResult {
  docId: string;
  anchor: string;
  text: string;
  page: number;
  version: number;
}

export interface QuotaLimits {
  max_docs: number;
  max_bytes: number;
  period: string;
}
export interface QuotaUsage {
  period: string;
  doc_count: number;
  bytes: number;
}
export interface QuotaInfo {
  limits: QuotaLimits;
  usage: QuotaUsage;
}

export interface AuditVerify {
  verified: boolean;
  rows: number;
  recomputed_mismatches: number;
  prev_hash_breaks: number;
  gaps: number;
  head_id: number | null;
  tail_id: number | null;
}

// ---- 模型配置（M）----
export type ModelKind = "llm" | "embedding" | "rerank";
export type ProviderType = "openai" | "anthropic" | "local" | "gemini";

export interface ModelConfig {
  id: string;
  name: string;
  kind: ModelKind;
  providerType: ProviderType;
  baseUrl: string;
  apiKey: string; // 脱敏（****xxxx）
  hasKey: boolean;
  modelName: string;
  dim: number | null;
  maxTokens: number | null;
  isDefault: boolean;
  system: boolean; // 系统内置（env 种子，只读）
}

export interface ModelDefault {
  id: string | null;
  name: string;
  providerType: ProviderType;
  baseUrl: string;
  modelName: string;
  dim: number | null;
}

export type ModelDefaults = Partial<Record<ModelKind, ModelDefault | null>>;

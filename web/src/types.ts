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
}

export interface Doc {
  docId: string;
  title: string;
  status: string;
  pages: number | null;
  sizeBytes: number | null;
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
  latency_ms: number;
  path_a_completed_rate?: number | null;
  path_a_degraded_reason?: string;
}

export interface SearchResult {
  hits: Hit[];
  route_stats: RouteStats;
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

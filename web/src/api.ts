import { api } from "./api/client";
import type { Doc, KB, Me, QuotaInfo, ReadAnchorResult, SearchResult, AuditVerify } from "./types";

// ---- 身份 ----
export const getMe = () => api.get<Me>("/v1/me").then((r) => r.data);

// ---- 知识库 ----
export const listKbs = () => api.get<KB[]>("/v1/kbs").then((r) => r.data);
export const createKb = (name: string, description?: string, visibility?: string) =>
  api.post<KB>("/v1/kbs", { name, description, visibility }).then((r) => r.data);

// ---- 文档 ----
export const listDocs = (kbId: string) =>
  api.get<Doc[]>(`/v1/kbs/${kbId}/docs`).then((r) => r.data);
export const uploadDoc = (kbId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return api
    .post(`/v1/kbs/${kbId}/docs`, form, { headers: { "Content-Type": "multipart/form-data" } })
    .then((r) => r.data);
};
export const readAnchor = (docId: string, anchor: string, before = 2, after = 4) =>
  api
    .post<ReadAnchorResult>("/v1/read-anchor", { docId, anchor, before, after })
    .then((r) => r.data);

// ---- 检索 ----
export const search = (
  query: string,
  knowledgeBaseIds?: string[],
  topK?: number,
  mode = "hybrid"
) =>
  api
    .post<SearchResult>("/v1/search", { query, knowledgeBaseIds, topK, mode })
    .then((r) => r.data);

// ---- ACL（admin）----
export const grant = (kbId: string, userId: string, role = "viewer", expiresAt?: string) =>
  api.put("/v1/acl", { kbId, userId, role, expiresAt }).then((r) => r.data);
export const revoke = (kbId: string, userId: string) =>
  api.delete("/v1/acl", { data: { kbId, userId } }).then((r) => r.data);

// ---- 运维（owner）----
export const gc = (fileId?: string, dryRun = true) =>
  api.post("/v1/admin/gc", { fileId, dryRun }).then((r) => r.data);
export const reconcile = (fileId?: string, dryRun = true, repair = true) =>
  api.post("/v1/admin/reconcile", { fileId, dryRun, repair }).then((r) => r.data);
export const pruneOutbox = (retainDays?: number) =>
  api.post("/v1/admin/outbox/prune", { retainDays }).then((r) => r.data);
export const auditVerify = () =>
  api.get<AuditVerify>("/v1/admin/audit/verify").then((r) => r.data);
export const auditAnchor = () =>
  api.post("/v1/admin/audit/anchor").then((r) => r.data);
export const getQuota = () =>
  api.get<QuotaInfo>("/v1/admin/quota").then((r) => r.data);

// ---- 监控 ----
export const readyz = () => api.get<Record<string, string>>("/readyz").then((r) => r.data);
export const fetchMetrics = () => api.get<string>("/metrics", { responseType: "text" }).then((r) => r.data);

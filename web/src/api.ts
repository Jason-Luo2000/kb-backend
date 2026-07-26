import { api } from "./api/client";
import type {
  AuditVerify,
  ChatModel,
  ChatResult,
  Doc,
  DriveFile,
  KB,
  Member,
  Me,
  ModelConfig,
  ModelDefaults,
  ModelKind,
  ParserConfig,
  ParserMethod,
  ProviderType,
  QuotaInfo,
  ReadAnchorResult,
  SearchResult,
  UserKb,
} from "./types";

// ---- 身份 ----
export const getMe = () => api.get<Me>("/v1/me").then((r) => r.data);

// ---- 知识库 ----
export const listKbs = () => api.get<KB[]>("/v1/kbs").then((r) => r.data);
export const createKb = (body: { name: string; description?: string; visibility?: string; parserConfig?: ParserConfig }) =>
  api.post<KB>("/v1/kbs", body).then((r) => r.data);
export const updateKb = (id: string, body: Partial<{ name: string; description: string; visibility: string; parserConfig: ParserConfig | null }>) =>
  api.patch<{ ok: boolean }>(`/v1/kbs/${id}`, body).then((r) => r.data);
export const getParserMethods = () =>
  api.get<ParserMethod[]>("/v1/parser/methods").then((r) => r.data);

// ---- 文档 ----
export const listDocs = (kbId: string) =>
  api.get<Doc[]>(`/v1/kbs/${kbId}/docs`).then((r) => r.data);
export const uploadDoc = (kbId: string, file: File, parseConfig?: ParserConfig) => {
  const form = new FormData();
  form.append("file", file);
  if (parseConfig) form.append("parseConfig", JSON.stringify(parseConfig));
  return api
    .post(`/v1/kbs/${kbId}/docs`, form, { headers: { "Content-Type": "multipart/form-data" } })
    .then((r) => r.data);
};
export const readAnchor = (docId: string, anchor: string, before = 2, after = 4) =>
  api
    .post<ReadAnchorResult>("/v1/read-anchor", { docId, anchor, before, after })
    .then((r) => r.data);

// ---- 个人文件库（F）----
export const uploadToDrive = (file: File, parseConfig?: ParserConfig) => {
  const form = new FormData();
  form.append("file", file);
  if (parseConfig) form.append("parseConfig", JSON.stringify(parseConfig));
  return api
    .post<{ fileId: string; status: string; reused?: boolean }>("/v1/files", form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};
export const listFiles = () => api.get<DriveFile[]>("/v1/files").then((r) => r.data);
export const deleteFile = (id: string) => api.delete(`/v1/files/${id}`).then((r) => r.data);
export const renameFile = (id: string, body: { name?: string; parseConfig?: ParserConfig }) =>
  api.patch(`/v1/files/${id}`, body).then((r) => r.data);
export const attachFile = (id: string, kbId: string, parseConfig?: ParserConfig) =>
  api.post<{ fileId: string; kbId: string; status: string; stats?: unknown }>(`/v1/files/${id}/attach`, {
    kbId,
    parseConfig,
  }).then((r) => r.data);
export const detachFile = (id: string, kbId: string) =>
  api.post(`/v1/files/${id}/detach`, { kbId }).then((r) => r.data);

// ---- 库内文档管理（F）----
export const removeDocFromKb = (kbId: string, docId: string) =>
  api.delete(`/v1/kbs/${kbId}/docs/${docId}`).then((r) => r.data);
export const reparseDoc = (kbId: string, docId: string, parseConfig?: ParserConfig) =>
  api.post<{ docId: string; stats: { version: number; chunks: number } }>(
    `/v1/kbs/${kbId}/docs/${docId}/reparse`,
    { parseConfig }
  ).then((r) => r.data);
export const renameDoc = (kbId: string, docId: string, title: string) =>
  api.patch(`/v1/kbs/${kbId}/docs/${docId}`, { title }).then((r) => r.data);
export const bulkDocs = (kbId: string, ids: string[], action: "delete" | "reparse") =>
  api.post(`/v1/kbs/${kbId}/docs/bulk`, { ids, action }).then((r) => r.data);

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

// ---- RAG 问答（/v1/chat）----
export const listChatModels = () =>
  api.get<ChatModel[]>("/v1/chat/models").then((r) => r.data);
export interface ChatParams {
  query: string;
  knowledgeBaseIds?: string[];
  modelId?: string;
  systemPrompt?: string;
  temperature?: number;
  maxTokens?: number;
  topK?: number;
  similarityThreshold?: number;
  rerank?: boolean;
  mode?: string;
  cite?: boolean;
}
export const chat = (body: ChatParams) =>
  api.post<ChatResult>("/v1/chat", body).then((r) => r.data);

// ---- ACL（admin）----
export const grant = (kbId: string, userId: string, role = "viewer", expiresAt?: string) =>
  api.put("/v1/acl", { kbId, userId, role, expiresAt }).then((r) => r.data);
export const revoke = (kbId: string, userId: string) =>
  api.delete("/v1/acl", { data: { kbId, userId } }).then((r) => r.data);

// ---- 成员管理（/v1/admin/users，admin）----
export const listUsers = (department?: string) =>
  api.get<Member[]>("/v1/admin/users", { params: department ? { department } : {} }).then((r) => r.data);
export const listDepartments = () =>
  api.get<string[]>("/v1/admin/users/departments").then((r) => r.data);
export const createUser = (body: { externalId: string; name?: string; department?: string; role?: string }) =>
  api.post<{ userId: string; apiKey: string }>("/v1/admin/users", body).then((r) => r.data);
export const updateUser = (id: string, body: { name?: string; department?: string; role?: string }) =>
  api.patch(`/v1/admin/users/${id}`, body).then((r) => r.data);
export const deleteUser = (id: string) =>
  api.delete(`/v1/admin/users/${id}`).then((r) => r.data);
export const userKbs = (id: string) =>
  api.get<UserKb[]>(`/v1/admin/users/${id}/kbs`).then((r) => r.data);
export const bulkGrant = (id: string, kbIds: string[], role = "viewer") =>
  api.post<{ ok: boolean; granted: number }>(`/v1/admin/users/${id}/kbs`, { kbIds, role }).then((r) => r.data);

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

// ---- 模型配置（M，owner）----
export interface ModelInput {
  name: string;
  kind: ModelKind;
  providerType: ProviderType;
  baseUrl?: string;
  apiKey?: string;
  modelName: string;
  dim?: number | null;
  maxTokens?: number | null;
  isDefault?: boolean;
}
export const listModels = () =>
  api.get<ModelConfig[]>("/v1/admin/models").then((r) => r.data);
export const getModelDefaults = () =>
  api.get<ModelDefaults>("/v1/admin/models/defaults").then((r) => r.data);
export const createModel = (body: ModelInput) =>
  api.post<{ id: string }>("/v1/admin/models", body).then((r) => r.data);
export const updateModel = (id: string, body: Partial<ModelInput>) =>
  api.patch<{ ok: boolean }>(`/v1/admin/models/${id}`, body).then((r) => r.data);
export const deleteModel = (id: string) =>
  api.delete<{ ok: boolean }>(`/v1/admin/models/${id}`).then((r) => r.data);
export const testModel = (id: string) =>
  api.post<{ ok: boolean; detail: string }>(`/v1/admin/models/${id}/test`).then((r) => r.data);

// ---- 监控 ----
export const readyz = () => api.get<Record<string, string>>("/readyz").then((r) => r.data);
export const fetchMetrics = () => api.get<string>("/metrics", { responseType: "text" }).then((r) => r.data);

/**
 * kb-client 1.0（T17）：kb-backend 的可复用 TS 客户端。
 * 幂等重试（review #15：仅 GET/search/cite/read_anchor/dry_run admin）+ 分级超时 +
 * 结构化错误（KbError，映射 HTTP 状态 + FastAPI detail → KB_*）+ FormData 上传。
 * 被 pi-ext 工具复用；也可被其它 TS 消费者直接 import makeKbClient。
 */

export class KbError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId?: string;
  constructor(code: string, message: string, status: number, requestId?: string) {
    super(`${code} (${status}): ${message}`);
    this.name = "KbError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

export interface KbClientOptions {
  baseUrl: string;
  token: string;
  apiVersion?: string;
  asUser?: string;
  maxRetries?: number;
  backoffBase?: number;
}

const TIMEOUTS = { default: 15_000, read: 8_000, admin: 30_000, upload: 120_000 } as const;
type TimeoutKey = keyof typeof TIMEOUTS;

const STATUS_DEFAULTS: Record<number, string> = {
  401: "KB_UNAUTHORIZED",
  403: "KB_FORBIDDEN_KB",
  404: "KB_NOT_FOUND",
  422: "KB_VALIDATION",
  429: "KB_RATE_LIMITED",
};

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export interface RequestOptions {
  body?: unknown;
  form?: FormData;
  idempotent?: boolean;
  timeoutKey?: TimeoutKey;
  idempotencyKey?: string;
}

export function makeKbClient(opts: KbClientOptions) {
  const base = opts.baseUrl.replace(/\/$/, "");
  const apiVersion = opts.apiVersion ?? "1";
  const maxRetries = opts.maxRetries ?? 3;
  const backoffBase = opts.backoffBase ?? 0.5;

  async function request<T>(method: string, path: string, ro: RequestOptions = {}): Promise<T> {
    const idempotent = ro.idempotent ?? false;
    const timeout = TIMEOUTS[ro.timeoutKey ?? "default"];
    const attempts = idempotent ? maxRetries + 1 : 1;
    const headers: Record<string, string> = {
      Authorization: `Bearer ${opts.token}`,
      "X-KB-Client": "kb-client/1.0",
      "X-KB-API-Version": apiVersion,
    };
    if (opts.asUser) headers["X-KB-User"] = opts.asUser;
    if (ro.idempotencyKey) headers["Idempotency-Key"] = ro.idempotencyKey;
    if (ro.body !== undefined) headers["Content-Type"] = "application/json";

    let lastErr: KbError | null = null;
    for (let i = 0; i < attempts; i++) {
      try {
        const r = await fetch(base + path, {
          method,
          headers,
          body: ro.form ?? (ro.body !== undefined ? JSON.stringify(ro.body) : undefined),
          signal: AbortSignal.timeout(timeout),
        });
        if (r.status >= 200 && r.status < 300) {
          return (await r.json()) as T;
        }
        let detail = "";
        let requestId: string | undefined;
        try {
          const j = (await r.json()) as { detail?: unknown; requestId?: string; request_id?: string };
          if (typeof j.detail === "string") detail = j.detail;
          else if (Array.isArray(j.detail))
            detail = j.detail.map((x: { msg?: string }) => x?.msg ?? String(x)).join("; ") || "validation error";
          requestId = j.requestId ?? j.request_id;
        } catch {
          /* 非 JSON body */
        }
        const code = detail.startsWith("KB_")
          ? detail
          : STATUS_DEFAULTS[r.status] ?? (r.status >= 500 ? "KB_SERVER_ERROR" : "KB_ERROR");
        const err = new KbError(code, detail || `HTTP ${r.status}`, r.status, requestId);
        if (!(r.status >= 500 || r.status === 429)) throw err; // 4xx 非重试
        lastErr = err;
      } catch (e) {
        if (e instanceof KbError) throw e; // 4xx 已直接抛
        lastErr = new KbError("KB_NETWORK", `network: ${(e as Error)?.name}: ${(e as Error)?.message}`, 0);
      }
      if (i < attempts - 1) {
        await sleep((backoffBase * 2 ** i + Math.random() * backoffBase) * 1000);
      }
    }
    throw lastErr ?? new KbError("KB_ERROR", "request failed", 0);
  }

  return {
    request,
    health: () => request<unknown>("GET", "/healthz", { idempotent: true }),
    listKbs: <T = unknown>() => request<T>("GET", "/v1/kbs", { idempotent: true }),
    getDoc: (docId: string) => request<unknown>("GET", `/v1/docs/${docId}`, { idempotent: true }),
    createKb: (name: string, description?: string, visibility?: string) =>
      request<unknown>("POST", "/v1/kbs", { body: { name, description, visibility } }),
    upload: (kbId: string, blob: Blob, filename: string) => {
      const form = new FormData();
      form.append("file", blob, filename);
      return request<unknown>("POST", `/v1/kbs/${kbId}/docs`, {
        form,
        timeoutKey: "upload",
        idempotencyKey: crypto.randomUUID(),
      });
    },
    search: (query: string, knowledgeBaseIds?: string[], topK?: number, mode = "hybrid") =>
      request<unknown>("POST", "/v1/search", {
        idempotent: true,
        timeoutKey: "read",
        body: { query, knowledgeBaseIds, topK, mode },
      }),
    readAnchor: (docId: string, anchor: string, before = 2, after = 4) =>
      request<unknown>("POST", "/v1/read-anchor", {
        idempotent: true,
        timeoutKey: "read",
        body: { docId, anchor, before, after },
      }),
    cite: (answer: string, chunkIds: string[]) =>
      request<unknown>("POST", "/v1/cite", { idempotent: true, timeoutKey: "read", body: { answer, chunkIds } }),
    grant: (kbId: string, userId: string, role = "viewer", expiresAt?: string) =>
      request<unknown>("PUT", "/v1/acl", { body: { kbId, userId, role, expiresAt } }),
    revoke: (kbId: string, userId: string) =>
      request<unknown>("DELETE", "/v1/acl", { body: { kbId, userId } }),
    gc: (fileId?: string, dryRun = true) =>
      request<unknown>("POST", "/v1/admin/gc", {
        idempotent: !!dryRun,
        timeoutKey: "admin",
        body: { fileId, dryRun },
      }),
    reconcile: (fileId?: string, dryRun = true, repair = true) =>
      request<unknown>("POST", "/v1/admin/reconcile", {
        idempotent: !!dryRun,
        timeoutKey: "admin",
        body: { fileId, dryRun, repair },
      }),
    pruneOutbox: (retainDays?: number) =>
      request<unknown>("POST", "/v1/admin/outbox/prune", { timeoutKey: "admin", body: { retainDays } }),
  };
}

export type KbClient = ReturnType<typeof makeKbClient>;

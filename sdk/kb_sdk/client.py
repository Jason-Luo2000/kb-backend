"""kb-sdk 1.0 客户端（T17）：幂等重试 + 分级超时 + 结构化错误 + 补全方法。

review #15：仅对幂等动词（GET/search/read_anchor/cite/dry_run admin）指数退避 + jitter 重试；
upload/grant/create_kb/admin-apply 默认不自动重试。分级超时（读 8s / admin 30s / upload 120s）。
upload 带 Idempotency-Key（前向兼容；服务端按 content_hash 去重）。
pip install -e sdk/ 后 from kb_sdk import KBClient。
"""
import random
import time
import uuid

import httpx

from .errors import KBError, from_response

_VERSION = "kb-sdk/1.0"
_DEFAULT_TIMEOUTS = {"default": 15.0, "read": 8.0, "admin": 30.0, "upload": 120.0}


class KBClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        user_id: str = "",
        *,
        api_version: str = "1",
        max_retries: int = 3,
        backoff_base: float = 0.5,
        timeouts: dict | None = None,
        transport: httpx.BaseTransport | None = None,  # 测试注入 MockTransport
    ):
        self.base = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeouts = {**_DEFAULT_TIMEOUTS, **(timeouts or {})}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-KB-Client": _VERSION,
            "X-KB-API-Version": api_version,  # 前向占位（服务端暂不协商）
        }
        if user_id:
            headers["X-KB-User"] = user_id
        self.http = httpx.Client(headers=headers, transport=transport)

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- 核心请求（带重试）----
    def _request(
        self,
        method: str,
        path: str,
        *,
        idempotent: bool,
        json=None,
        files=None,
        params=None,
        timeout_key: str = "default",
        idempotency_key: str | None = None,
    ):
        url = self.base + path
        timeout = self.timeouts[timeout_key]
        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        attempts = self.max_retries + 1 if idempotent else 1  # 非幂等：1 次，不重试（#15）
        last_exc: KBError | None = None
        for i in range(attempts):
            try:
                r = self.http.request(
                    method, url, json=json, files=files, params=params, headers=extra_headers, timeout=timeout
                )
            except httpx.HTTPError as e:  # 网络/超时/连接异常 → 可重试（幂等时）
                last_exc = KBError(f"network: {type(e).__name__}: {e}", status=0, code="KB_NETWORK")
            else:
                if 200 <= r.status_code < 300:
                    return r.json()
                err = from_response(r)
                if not (r.status_code >= 500 or r.status_code == 429):
                    raise err  # 4xx 非重试，立即抛
                last_exc = err  # 5xx / 429 → 重试
            if i < attempts - 1:
                time.sleep(self.backoff_base * (2 ** i) + random.uniform(0, self.backoff_base))
        raise last_exc or KBError("request failed", status=0)

    # ---- 基础 ----
    def health(self) -> dict:
        return self._request("GET", "/healthz", idempotent=True)

    def list_kbs(self) -> list[dict]:
        return self._request("GET", "/v1/kbs", idempotent=True)

    def get_doc(self, doc_id: str) -> dict:
        return self._request("GET", f"/v1/docs/{doc_id}", idempotent=True)

    def create_kb(self, name: str, description: str | None = None, visibility: str | None = None) -> dict:
        body: dict = {"name": name, "description": description}
        if visibility is not None:  # None 时不发键，让服务端用默认 'team'（列 NOT NULL）
            body["visibility"] = visibility
        return self._request("POST", "/v1/kbs", idempotent=False, json=body)

    def upload(self, kb_id: str, path: str) -> dict:
        with open(path, "rb") as f:
            return self._request(
                "POST", f"/v1/kbs/{kb_id}/docs", idempotent=False, files={"file": f},
                timeout_key="upload", idempotency_key=str(uuid.uuid4()),  # 前向兼容（服务端按 content_hash 去重）
            )

    # ---- 检索（幂等，重试）----
    def search(
        self, query: str, knowledge_base_ids: list[str] | None = None,
        top_k: int | None = None, mode: str = "hybrid",
    ) -> dict:
        return self._request(
            "POST", "/v1/search", idempotent=True, timeout_key="read",
            json={"query": query, "knowledgeBaseIds": knowledge_base_ids, "topK": top_k, "mode": mode},
        )

    def read_anchor(self, doc_id: str, anchor: str, before: int = 2, after: int = 4) -> dict:
        return self._request(
            "POST", "/v1/read-anchor", idempotent=True, timeout_key="read",
            json={"docId": doc_id, "anchor": anchor, "before": before, "after": after},
        )

    def cite(self, answer: str, chunk_ids: list[str]) -> dict:
        return self._request("POST", "/v1/cite", idempotent=True, timeout_key="read",
                             json={"answer": answer, "chunkIds": chunk_ids})

    # ---- ACL（非幂等，不重试）----
    def grant(self, kb_id: str, user_id: str, role: str = "viewer", expires_at: str | None = None) -> dict:
        return self._request(
            "PUT", "/v1/acl", idempotent=False,
            json={"kbId": kb_id, "userId": user_id, "role": role, "expiresAt": expires_at},
        )

    def revoke(self, kb_id: str, user_id: str) -> dict:
        return self._request("DELETE", "/v1/acl", idempotent=False, json={"kbId": kb_id, "userId": user_id})

    # ---- 运维（dry_run 幂等重试；apply 非幂等不重试；owner-only）----
    def gc(self, file_id: str | None = None, dry_run: bool = True) -> dict:
        return self._request(
            "POST", "/v1/admin/gc", idempotent=bool(dry_run), timeout_key="admin",
            json={"fileId": file_id, "dryRun": dry_run},
        )

    def reconcile(self, file_id: str | None = None, dry_run: bool = True, repair: bool = True) -> dict:
        return self._request(
            "POST", "/v1/admin/reconcile", idempotent=bool(dry_run), timeout_key="admin",
            json={"fileId": file_id, "dryRun": dry_run, "repair": repair},
        )

    def prune_outbox(self, retain_days: int | None = None) -> dict:
        return self._request(
            "POST", "/v1/admin/outbox/prune", idempotent=False, timeout_key="admin",
            json={"retainDays": retain_days},
        )

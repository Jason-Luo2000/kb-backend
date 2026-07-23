"""Python SDK：kb-backend 的瘦客户端。pip install -e sdk/ 后 from kb_sdk import KBClient。"""
import httpx


class KBClient:
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "", user_id: str = ""):
        self.base = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}", "X-KB-Client": "kb-sdk/0.1"}
        if user_id:
            headers["X-KB-User"] = user_id
        self.http = httpx.Client(timeout=120, headers=headers)

    def _req(self, method: str, path: str, **kw):
        r = self.http.request(method, self.base + path, **kw)
        r.raise_for_status()
        return r.json()

    def health(self) -> dict:
        return self.http.get(self.base + "/healthz").json()

    def list_kbs(self) -> list[dict]:
        return self._req("GET", "/v1/kbs")

    def create_kb(self, name: str, description: str | None = None) -> dict:
        return self._req("POST", "/v1/kbs", json={"name": name, "description": description})

    def upload(self, kb_id: str, path: str) -> dict:
        with open(path, "rb") as f:
            return self._req("POST", f"/v1/kbs/{kb_id}/docs", files={"file": f})

    def search(self, query: str, knowledge_base_ids: list[str] | None = None, top_k: int | None = None, mode: str = "hybrid") -> dict:
        return self._req(
            "POST",
            "/v1/search",
            json={"query": query, "knowledgeBaseIds": knowledge_base_ids, "topK": top_k, "mode": mode},
        )

    def read_anchor(self, doc_id: str, anchor: str, before: int = 2, after: int = 4) -> dict:
        return self._req("POST", "/v1/read-anchor", json={"docId": doc_id, "anchor": anchor, "before": before, "after": after})

    def cite(self, answer: str, chunk_ids: list[str]) -> dict:
        return self._req("POST", "/v1/cite", json={"answer": answer, "chunkIds": chunk_ids})

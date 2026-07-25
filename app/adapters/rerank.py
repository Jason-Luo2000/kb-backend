"""Rerank provider 抽象（M，可选）：配了 rerank 模型才走，否则 orchestrator 保持 RRF 融合不变。

OpenAI 兼容 /rerank 形状（Cohere / Jina / Voyage / bge-reranker-api 通用）：
  POST {base_url}/rerank  {model, query, documents:[str], top_n}  →  {results:[{index, relevance_score}]}
模块级 rerank() 解析生效配置；未配置 / 调用失败 → 返回 None（调用方回退原序 RRF）。
"""
import httpx

from app.models_registry import ModelConfig, resolve_model


class RerankProvider:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg

    def rerank(self, query: str, docs: list[str]) -> list[dict]:
        raise NotImplementedError


class HttpReranker(RerankProvider):
    def __init__(self, cfg: ModelConfig):
        super().__init__(cfg)
        self._http = httpx.Client(timeout=30)

    def rerank(self, query: str, docs: list[str]) -> list[dict]:
        url = (self.cfg.base_url or "").rstrip("/") + "/rerank"
        resp = self._http.post(
            url,
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json={"model": self.cfg.model_name, "query": query, "documents": docs, "top_n": len(docs)},
        )
        resp.raise_for_status()
        return resp.json()["results"]


_cache: dict[tuple, RerankProvider] = {}


def _client(cfg: ModelConfig) -> RerankProvider:
    key = ("rerank", cfg.base_url, cfg.api_key, cfg.model_name)
    c = _cache.get(key)
    if c is None:
        c = HttpReranker(cfg)
        _cache[key] = c
    return c


def rerank(query: str, docs: list[str], tenant_id: str | None = None) -> list[tuple[int, float]] | None:
    """返回按分数降序的 [(orig_index, score)]；未配置/失败返回 None（调用方回退 RRF）。"""
    cfg = resolve_model("rerank", tenant_id)
    if cfg is None or not cfg.api_key or not docs:
        return None
    try:
        results = _client(cfg).rerank(query, docs)
    except Exception as e:  # noqa: BLE001
        print(f"[rerank] 不可用（{type(e).__name__}: {str(e)[:80]}），回退原序 RRF")
        return None
    return sorted(((int(r["index"]), float(r["relevance_score"])) for r in results), key=lambda x: -x[1])


def test_model(cfg: ModelConfig) -> str:
    """测连通（owner 端点）：一次 rerank。失败抛异常由调用方捕获。"""
    res = _client(cfg).rerank("query", ["doc one", "doc two"])
    return f"ok: {len(res)} scored"

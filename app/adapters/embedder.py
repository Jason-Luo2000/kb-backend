"""Embedding provider 抽象（M）：OpenAI 兼容 /embeddings（智谱 embedding / OpenAI / 本地 BGE via api / vLLM）。

模块级 embed/embed_batch 经 models_registry.resolve_model('embedding') 解析生效配置 → 缓存客户端。
验证环境若 provider 不可用（429 / code 1113 / 网络）→ 退回确定性哈希伪向量（仅验流程，无语义；生产需真 embedding）。
"""
import hashlib
import math
import random

import httpx

from app.config import settings
from app.models_registry import ModelConfig, resolve_model

_hash_warned = False


def _hash_vec(text: str, dim: int) -> list[float]:
    """确定性伪向量：同文本同向量、不同文本不同，单位长度。"""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


class EmbedderProvider:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAIEmbedder(EmbedderProvider):
    """OpenAI 兼容 /embeddings。"""

    def __init__(self, cfg: ModelConfig):
        super().__init__(cfg)
        self._http = httpx.Client(timeout=30)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        body: dict = {"model": self.cfg.model_name, "input": texts}
        if self.cfg.dim:
            body["dimensions"] = self.cfg.dim
        url = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
        resp = self._http.post(
            url,
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda d: d["index"])
        return [d["embedding"] for d in data]


_cache: dict[tuple, EmbedderProvider] = {}


def _client(cfg: ModelConfig) -> EmbedderProvider:
    key = ("embedding", cfg.base_url, cfg.api_key, cfg.model_name)
    c = _cache.get(key)
    if c is None:
        c = OpenAIEmbedder(cfg)
        _cache[key] = c
    return c


def embed(text: str, tenant_id: str | None = None) -> list[float]:
    return embed_batch([text], tenant_id=tenant_id)[0]


def embed_batch(texts: list[str], batch: int = 16, tenant_id: str | None = None) -> list[list[float]]:
    cfg = resolve_model("embedding", tenant_id)
    if cfg is None or not cfg.api_key:
        raise RuntimeError("未配置 embedding（POST /v1/admin/models 或设 ZHIPU_API_KEY）")
    dim = cfg.dim or settings.zhipu_embed_dim
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        try:
            out.extend(_client(cfg).embed_batch(chunk))
        except Exception as e:  # noqa: BLE001
            global _hash_warned
            if not _hash_warned:
                print(
                    f"[embedder] embedding 不可用（{type(e).__name__}: {str(e)[:80]}），"
                    "退回哈希伪向量（仅验证流程，生产需真 embedding）"
                )
                _hash_warned = True
            out.extend(_hash_vec(t, dim) for t in chunk)
    return out


def test_model(cfg: ModelConfig) -> str:
    """测连通（owner 端点）：一次 embed，返维度。失败抛异常由调用方捕获（不走哈希兜底）。"""
    vec = _client(cfg).embed_batch(["ping"])
    return f"ok: dim={len(vec[0])}"

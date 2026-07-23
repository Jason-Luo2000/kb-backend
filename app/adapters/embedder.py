"""Embedding 适配器：智谱 embedding（OpenAI 兼容端点）。生产用。
验证环境若智谱 embedding 余额不足（429 / code 1113），退回确定性哈希伪向量——
仅用于验证业务流程（双路编排/RRF/总结/引用），无语义；生产需真 embedding（充值或本地 BGE-M3）。"""
import hashlib
import math
import random

import httpx

from app.config import settings

_http = httpx.Client(timeout=30)
_hash_warned = False


def _hash_vec(text: str, dim: int) -> list[float]:
    """确定性伪向量：同文本同向量、不同文本不同，单位长度。"""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _embed_raw(texts: list[str]) -> list[list[float]]:
    resp = _http.post(
        f"{settings.zhipu_embed_base_url}/embeddings",
        headers={"Authorization": f"Bearer {settings.zhipu_api_key}"},
        json={"model": settings.zhipu_embed_model, "input": texts, "dimensions": settings.zhipu_embed_dim},
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def embed(text: str) -> list[float]:
    return embed_batch([text])[0]


def embed_batch(texts: list[str], batch: int = 16) -> list[list[float]]:
    if not settings.zhipu_api_key:
        raise RuntimeError("ZHIPU_API_KEY 未配置")
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        try:
            out.extend(_embed_raw(chunk))
        except Exception as e:  # noqa: BLE001
            global _hash_warned
            if not _hash_warned:
                print(
                    f"[embedder] 智谱 embedding 不可用（{type(e).__name__}: {str(e)[:80]}），"
                    "退回哈希伪向量（仅验证流程，生产需真 embedding）"
                )
                _hash_warned = True
            out.extend(_hash_vec(t, settings.zhipu_embed_dim) for t in chunk)
    return out

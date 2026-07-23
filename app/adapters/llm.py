"""LLM 适配器：MVP 用智谱 glm-5.2（Anthropic 兼容端点 open.bigmodel.cn/api/anthropic）。
复用 pi 同一套 key。后期可切 OpenAI 兼容 / 本地 vLLM（改本文件）。"""
from anthropic import Anthropic

from app.config import settings

_client: Anthropic | None = None


def _client_get() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(
            api_key=settings.zhipu_api_key,
            base_url=settings.zhipu_llm_base_url,
        )
    return _client


def chat(prompt: str, system: str | None = None, max_tokens: int = 4096) -> str:
    """同步对话，返回纯文本。"""
    if not settings.zhipu_api_key:
        raise RuntimeError("ZHIPU_API_KEY 未配置")
    kwargs: dict = {
        "model": settings.zhipu_llm_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    resp = _client_get().messages.create(**kwargs)
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")

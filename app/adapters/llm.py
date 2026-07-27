"""LLM provider 抽象（M：多 provider 运行时切换）。

provider_type → 适配：
- anthropic → Anthropic SDK（anthropic 兼容 /v1/messages 端点）
- openai | local → OpenAI 兼容 /chat/completions（httpx；覆盖 OpenAI/DeepSeek/Moonshot/本地 vLLM/Ollama 等）
- gemini → 原生 API 形状不同，stub（NotImplementedError；用 OpenAI 兼容代理或 Anthropic 适配）

模块级 chat() 经 models_registry.resolve_model('llm') 解析生效配置（租户默认→系统内置→env 兜底）→
按 (provider_type,base_url,api_key,model) 缓存客户端 → 调用。调用方（summarizer/orchestrator）签名不变。
"""
import httpx
from anthropic import Anthropic

from app.config import settings
from app.models_registry import ModelConfig, resolve_model


class LLMProvider:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg

    def chat(self, prompt: str, system: str | None = None, max_tokens: int = 4096) -> str:
        raise NotImplementedError


class AnthropicLLM(LLMProvider):
    def __init__(self, cfg: ModelConfig):
        super().__init__(cfg)
        kwargs: dict = {"api_key": cfg.api_key}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self._c = Anthropic(**kwargs)

    def chat(self, prompt: str, system: str | None = None, max_tokens: int = 4096,
             temperature: float | None = None) -> str:
        kwargs: dict = {
            "model": self.cfg.model_name,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = self._c.messages.create(**kwargs)
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


class OpenAILLM(LLMProvider):
    """OpenAI 兼容 /chat/completions（OpenAI / DeepSeek / Moonshot / 本地 vLLM / Ollama 等）。"""

    def __init__(self, cfg: ModelConfig):
        super().__init__(cfg)
        self._http = httpx.Client(timeout=60)

    def chat(self, prompt: str, system: str | None = None, max_tokens: int = 4096,
             temperature: float | None = None) -> str:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        url = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        body: dict = {"model": self.cfg.model_name, "max_tokens": max_tokens, "messages": msgs}
        if temperature is not None:
            body["temperature"] = temperature
        resp = self._http.post(
            url,
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class GeminiLLM(LLMProvider):
    def chat(self, *args, **kwargs):  # noqa: D401, ANN
        raise NotImplementedError("Gemini 原生 API 暂未适配；请用 OpenAI 兼容代理或选 Anthropic 适配")


_cache: dict[tuple, LLMProvider] = {}


def _client(cfg: ModelConfig) -> LLMProvider:
    key = ("llm", cfg.provider_type, cfg.base_url, cfg.api_key, cfg.model_name)
    c = _cache.get(key)
    if c is None:
        pt = cfg.provider_type
        if pt == "anthropic":
            c = AnthropicLLM(cfg)
        elif pt == "gemini":
            c = GeminiLLM(cfg)
        else:  # openai | local（及其它 OpenAI 兼容 provider）
            c = OpenAILLM(cfg)
        _cache[key] = c
    return c


def chat(prompt: str, system: str | None = None, max_tokens: int | None = None, tenant_id: str | None = None) -> str:
    """同步对话，返回纯文本。max_tokens 省略时用模型级配置（→ default_llm_max_tokens 兜底）。"""
    cfg = resolve_model("llm", tenant_id)
    if cfg is None or not cfg.api_key:
        raise RuntimeError("未配置 LLM（POST /v1/admin/models 或设 MODEL_API_KEY + LLM_* env 种子）")
    mt = max_tokens if max_tokens is not None else (cfg.max_tokens or settings.default_llm_max_tokens)
    return _client(cfg).chat(prompt, system, mt)


def generate(
    prompt: str,
    system: str | None = None,
    *,
    model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    tenant_id: str | None = None,
) -> tuple[str, str]:
    """RAG 生成（/v1/chat 用）。model_id 指定则按 id 解析（get_model_config），否则租户默认。
    返回 (text, model_name)。未配置→抛 RuntimeError（调用方降级）。"""
    from app.models_registry import get_model_config

    if model_id:
        cfg = get_model_config(model_id, tenant_id)
        if cfg is None or cfg.kind != "llm":
            raise RuntimeError("KB_MODEL_NOT_FOUND")
    else:
        cfg = resolve_model("llm", tenant_id)
    if cfg is None or not cfg.api_key:
        raise RuntimeError("未配置 LLM")
    mt = max_tokens if max_tokens is not None else (cfg.max_tokens or settings.default_llm_max_tokens)
    text = _client(cfg).chat(prompt, system, mt, temperature)
    return text, cfg.model_name


def test_model(cfg: ModelConfig) -> str:
    """测连通（owner 端点）：一次极小 chat。失败抛异常由调用方捕获。"""
    txt = _client(cfg).chat("ping", max_tokens=8)
    return f"ok: {len(txt)} chars"

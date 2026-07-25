"""LLM provider 抽象（M：多 provider 运行时切换）。

provider_type → 适配：
- anthropic → Anthropic SDK（智谱 glm 的 anthropic 兼容端点 open.bigmodel.cn/api/anthropic 也走这条）
- openai | zhipu | local → OpenAI 兼容 /chat/completions（httpx；覆盖 OpenAI/智谱-openai/本地 vLLM/Ollama/DeepSeek 等）
- gemini → 原生 API 形状不同，stub（NotImplementedError；用 OpenAI 兼容代理或 Anthropic 适配）

模块级 chat() 经 models_registry.resolve_model('llm') 解析生效配置（租户默认→系统内置→env 兜底）→
按 (provider_type,base_url,api_key,model) 缓存客户端 → 调用。调用方（summarizer/orchestrator）签名不变。
"""
import httpx
from anthropic import Anthropic

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

    def chat(self, prompt: str, system: str | None = None, max_tokens: int = 4096) -> str:
        kwargs: dict = {
            "model": self.cfg.model_name,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = self._c.messages.create(**kwargs)
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


class OpenAILLM(LLMProvider):
    """OpenAI 兼容 /chat/completions（OpenAI / 智谱 openai 端点 / 本地 vLLM / Ollama / DeepSeek 等）。"""

    def __init__(self, cfg: ModelConfig):
        super().__init__(cfg)
        self._http = httpx.Client(timeout=60)

    def chat(self, prompt: str, system: str | None = None, max_tokens: int = 4096) -> str:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        url = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        resp = self._http.post(
            url,
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json={"model": self.cfg.model_name, "max_tokens": max_tokens, "messages": msgs},
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
        else:  # openai | zhipu | local
            c = OpenAILLM(cfg)
        _cache[key] = c
    return c


def chat(prompt: str, system: str | None = None, max_tokens: int = 4096, tenant_id: str | None = None) -> str:
    """同步对话，返回纯文本。tenant_id 省略时读 contextvar（auth 中间件 set）。"""
    cfg = resolve_model("llm", tenant_id)
    if cfg is None or not cfg.api_key:
        raise RuntimeError("未配置 LLM（POST /v1/admin/models 或设 ZHIPU_API_KEY）")
    return _client(cfg).chat(prompt, system, max_tokens)


def test_model(cfg: ModelConfig) -> str:
    """测连通（owner 端点）：一次极小 chat。失败抛异常由调用方捕获。"""
    txt = _client(cfg).chat("ping", max_tokens=8)
    return f"ok: {len(txt)} chars"

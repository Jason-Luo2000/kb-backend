"""M 阶段：模型 provider 注册表单测。
- crypto 往返 / 空值 / 换 key
- resolve 顺序：租户默认 > 系统内置 > env 兜底；rerank 未配置=None
- provider 分派（无网络：仅查类）
- owner-only 端点（TestClient + dev owner key）
运行：.venv/bin/pytest tests/test_models.py -q   （需 dev DB 已 bootstrap）"""
import pytest
from starlette.testclient import TestClient

from app import crypto
from app.adapters import embedder, llm
from app.bootstrap import default_tenant_id
from app.main import app
from app.models_registry import (
    ModelConfig,
    create_model,
    delete_model,
    resolve_model,
)

AUTH = {"Authorization": "Bearer kb_dev_api_key"}


# ============ crypto ============
def test_crypto_roundtrip():
    assert crypto.decrypt(crypto.encrypt("sk-secret")) == "sk-secret"


def test_crypto_empty():
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""


def test_crypto_wrong_key(monkeypatch):
    token = crypto.encrypt("sk-secret")
    from app import crypto as cmod
    monkeypatch.setattr(cmod.settings, "model_secret", "a-different-secret")
    cmod._instance = None  # 清缓存
    assert crypto.decrypt(token) == ""  # 换 key → 解不出 → 空串（非抛）
    cmod._instance = None  # 复位


# ============ resolve 顺序 ============
def test_resolve_env_fallback_when_no_tenant():
    cfg = resolve_model("embedding")
    assert cfg is not None and cfg.model_name  # env 兜底（DB 可能无系统行时仍工作）


def test_resolve_rerank_none_when_unconfigured():
    assert resolve_model("rerank") is None


def test_resolve_tenant_overrides_system():
    tid = default_tenant_id()
    # 先确认系统默认存在（bootstrap 种）
    sys_cfg = resolve_model("llm", None)
    assert sys_cfg is not None
    # 种一个租户 llm 默认（openai 兼容、假 model 名）
    created = create_model(tid, {
        "name": "test-tenant-llm", "kind": "llm", "providerType": "openai",
        "baseUrl": "http://localhost:11111/v1", "apiKey": "sk-test",
        "modelName": "gpt-test", "isDefault": True,
    })
    mid = created["id"]
    try:
        cfg = resolve_model("llm", tid)
        assert cfg.model_name == "gpt-test"  # 租户默认覆盖系统
        assert resolve_model("llm", None).model_name == sys_cfg.model_name  # 系统行不受影响
    finally:
        delete_model(mid, tid)


# ============ provider 分派（无网络）============
def _cfg(ptype):
    return ModelConfig(None, "llm", ptype, "http://x/v1", "sk-x", "m", None)


def test_provider_dispatch_classes():
    assert isinstance(llm._client(_cfg("anthropic")), llm.AnthropicLLM)
    assert isinstance(llm._client(_cfg("openai")), llm.OpenAILLM)
    assert isinstance(llm._client(_cfg("deepseek")), llm.OpenAILLM)  # 其它 openai 兼容 provider 走 OpenAILLM
    assert isinstance(llm._client(_cfg("local")), llm.OpenAILLM)


def test_gemini_stub_raises():
    with pytest.raises(NotImplementedError):
        llm._client(_cfg("gemini")).chat("ping")


def test_embedding_client():
    e = embedder._client(ModelConfig(None, "embedding", "openai", "http://x/v1", "sk", "m", 8))
    assert isinstance(e, embedder.OpenAIEmbedder)


# ============ owner-only 端点 ============
def test_models_endpoint_requires_auth():
    with TestClient(app) as c:
        assert c.get("/v1/admin/models").status_code == 401


def test_models_crud_and_defaults():
    with TestClient(app) as c:
        # 列表（含系统种子）
        r = c.get("/v1/admin/models", headers=AUTH)
        assert r.status_code == 200
        kinds = {m["kind"] for m in r.json()}
        assert {"llm", "embedding"}.issubset(kinds)
        # 系统行 key 脱敏
        sysrow = next(m for m in r.json() if m["system"])
        assert "*" in sysrow["apiKey"] or sysrow["apiKey"] == ""

        # defaults
        d = c.get("/v1/admin/models/defaults", headers=AUTH).json()
        assert d["llm"] is not None and d["rerank"] is None

        # 建租户 rerank（验证 rerank 未配→配 后 default 变化）
        body = {"name": "test-rerank", "kind": "rerank", "providerType": "openai",
                "baseUrl": "http://localhost:11111/v1", "apiKey": "sk-r",
                "modelName": "bge-rerank", "isDefault": True}
        cr = c.post("/v1/admin/models", headers=AUTH, json=body)
        assert cr.status_code == 200
        mid = cr.json()["id"]
        try:
            d2 = c.get("/v1/admin/models/defaults", headers=AUTH).json()
            assert d2["rerank"] is not None and d2["rerank"]["modelName"] == "bge-rerank"
            # patch（改 model 名 + 取消默认）
            assert c.patch(f"/v1/admin/models/{mid}", headers=AUTH,
                           json={"modelName": "rerank-v2"}).status_code == 200
            # delete
            assert c.delete(f"/v1/admin/models/{mid}", headers=AUTH).status_code == 200
            assert c.delete(f"/v1/admin/models/{mid}", headers=AUTH).status_code == 404
        finally:
            delete_model(mid, default_tenant_id())  # 兜底清理


def test_models_create_validation():
    with TestClient(app) as c:
        # 缺 modelName
        assert c.post("/v1/admin/models", headers=AUTH,
                      json={"kind": "llm"}).status_code == 400
        # 非法 kind
        assert c.post("/v1/admin/models", headers=AUTH,
                      json={"modelName": "x", "kind": "bogus"}).status_code == 400

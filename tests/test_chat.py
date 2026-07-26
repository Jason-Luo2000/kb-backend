"""阶段 1：RAG 问答（/v1/chat + /v1/chat/models）集成测。LLM 用 monkeypatch 假造（不依赖真模型）。
检索走真路径（hash 伪向量兜底），需 MinIO+ES 栈。运行：.venv/bin/pytest tests/test_chat.py -q"""
import os
import uuid

import pytest
from starlette.testclient import TestClient

from app.main import app

AUTH = {"Authorization": "Bearer kb_dev_api_key"}
SAMPLE = os.path.join(os.path.dirname(__file__), "..", "scripts", "sample.md")


def _stack_ready():
    try:
        from app.config import settings
        from app.storage import get_minio

        return get_minio().bucket_exists(settings.minio_bucket)
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _stack_ready(), reason="needs MinIO+ES stack (dev stack)")


def _unique_md() -> bytes:
    with open(SAMPLE, "rb") as f:
        return f.read() + f"\n\n# chat-marker {uuid.uuid4().hex}\n".encode()


def _kb_with_doc(c):
    kid = c.post("/v1/kbs", headers=AUTH, json={"name": "chat-" + uuid.uuid4().hex[:6]}).json()["id"]
    up = c.post(f"/v1/kbs/{kid}/docs", headers=AUTH,
                files={"file": ("d.md", _unique_md(), "text/markdown")}).json()
    return kid, up["docId"]


def _fake_generate(prompt, system=None, *, model_id=None, temperature=None, max_tokens=None, tenant_id=None):
    return ("根据资料，答案是 42。[1]", "test-llm")


def test_chat_models_redacted():
    with TestClient(app) as c:
        rows = c.get("/v1/chat/models", headers=AUTH).json()
        assert rows, "应至少有种子的 llm 模型"
        for r in rows:
            assert set(r.keys()) == {"id", "name", "modelName", "isDefault"}  # 脱敏：无 apiKey/baseUrl


def test_chat_generates_answer_with_references(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.generate", _fake_generate)
    with TestClient(app) as c:
        kid, doc_id = _kb_with_doc(c)
        r = c.post("/v1/chat", headers=AUTH, json={"query": "答案是什么", "knowledgeBaseIds": [kid]}).json()
        try:
            assert r["answer"] == "根据资料，答案是 42。[1]"
            assert r["model"] == "test-llm"
            assert r["error"] is None
            assert len(r["references"]) >= 1          # 有引用来源
            assert r["references"][0]["docId"] == doc_id
            assert len(r["hits"]) >= 1                 # 同时返回命中
            assert "rerank_used" in r["route_stats"]
        finally:
            c.delete(f"/v1/files/{doc_id}", headers=AUTH)


def test_chat_threshold_filters_all(monkeypatch):
    """极高 similarity_threshold → 命中全过滤 → 走"未检索到"分支。"""
    monkeypatch.setattr("app.adapters.llm.generate", _fake_generate)
    with TestClient(app) as c:
        kid, doc_id = _kb_with_doc(c)
        try:
            r = c.post("/v1/chat", headers=AUTH, json={
                "query": "x", "knowledgeBaseIds": [kid], "similarityThreshold": 999}).json()
            assert r["hits"] == [] and r["references"] == []
            assert "未检索到" in r["answer"]
        finally:
            c.delete(f"/v1/files/{doc_id}", headers=AUTH)


def test_chat_model_not_found_400():
    # 真 generate：bad modelId → get_model_config 返 None → 抛 KB_MODEL_NOT_FOUND（不触网）
    with TestClient(app) as c:
        kid, doc_id = _kb_with_doc(c)
        try:
            r = c.post("/v1/chat", headers=AUTH,
                       json={"query": "x", "knowledgeBaseIds": [kid], "modelId": str(uuid.uuid4())})
            assert r.status_code == 400 and r.json()["detail"] == "KB_MODEL_NOT_FOUND"
        finally:
            c.delete(f"/v1/files/{doc_id}", headers=AUTH)


def test_chat_llm_failure_degrades(monkeypatch):
    """LLM 调用失败 → answer=null + error，但命中仍返回（降级为纯检索）。"""
    def boom(*a, **k):
        raise RuntimeError("未配置 LLM")

    monkeypatch.setattr("app.adapters.llm.generate", boom)
    with TestClient(app) as c:
        kid, doc_id = _kb_with_doc(c)
        try:
            r = c.post("/v1/chat", headers=AUTH, json={"query": "x", "knowledgeBaseIds": [kid]}).json()
            assert r["answer"] is None and r["error"]
            assert len(r["hits"]) >= 1  # 检索结果仍在
        finally:
            c.delete(f"/v1/files/{doc_id}", headers=AUTH)

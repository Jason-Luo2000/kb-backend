"""问答会话（/v1/chat/conversations）集成测：CRUD + /v1/chat 持久化 + 用户隔离。
LLM 用 monkeypatch；需 MinIO+ES 栈。运行：.venv/bin/pytest tests/test_conversations.py -q"""
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


def _fake_generate(prompt, system=None, *, model_id=None, temperature=None, max_tokens=None, tenant_id=None):
    return ("答案 [1]", "test-llm")


def _kb_with_doc(c):
    with open(SAMPLE, "rb") as f:
        data = f.read() + f"\n\n# cv-marker {uuid.uuid4().hex}\n".encode()
    kid = c.post("/v1/kbs", headers=AUTH, json={"name": "cv-" + uuid.uuid4().hex[:6]}).json()["id"]
    doc = c.post(f"/v1/kbs/{kid}/docs", headers=AUTH, files={"file": ("d.md", data, "text/markdown")}).json()["docId"]
    return kid, doc


def test_conversation_crud():
    with TestClient(app) as c:
        cid = c.post("/v1/chat/conversations", headers=AUTH, json={"title": "测试会话"}).json()["id"]
        lst = c.get("/v1/chat/conversations", headers=AUTH).json()
        assert any(x["id"] == cid and x["title"] == "测试会话" for x in lst)
        assert c.get(f"/v1/chat/conversations/{cid}", headers=AUTH).json()["messages"] == []
        c.patch(f"/v1/chat/conversations/{cid}", headers=AUTH, json={"title": "改名"})
        assert c.get(f"/v1/chat/conversations/{cid}", headers=AUTH).json()["title"] == "改名"
        assert c.delete(f"/v1/chat/conversations/{cid}", headers=AUTH).status_code == 200
        assert not any(x["id"] == cid for x in c.get("/v1/chat/conversations", headers=AUTH).json())


def test_chat_persists_to_conversation(monkeypatch):
    """/v1/chat 带 conversationId → 消息落库 + 自动标题。"""
    monkeypatch.setattr("app.adapters.llm.generate", _fake_generate)
    with TestClient(app) as c:
        kid, doc = _kb_with_doc(c)
        cid = c.post("/v1/chat/conversations", headers=AUTH, json={}).json()["id"]
        try:
            c.post("/v1/chat", headers=AUTH, json={"query": "Q1", "knowledgeBaseIds": [kid], "conversationId": cid})
            c.post("/v1/chat", headers=AUTH, json={"query": "Q2", "knowledgeBaseIds": [kid], "conversationId": cid})
            conv = c.get(f"/v1/chat/conversations/{cid}", headers=AUTH).json()
            assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]
            assert conv["messages"][0]["content"] == "Q1"
            assert conv["messages"][1]["content"] == "答案 [1]"
            assert conv["title"] == "Q1"  # 首轮 query 自动命名
        finally:
            c.delete(f"/v1/files/{doc}", headers=AUTH)


def test_conversation_ownership_isolated():
    """用户 B 看不到 / 取不到 / 删不了 用户 A 的会话。"""
    with TestClient(app) as c:
        from tests.test_members import _create_user

        cid = c.post("/v1/chat/conversations", headers=AUTH, json={"title": "mine"}).json()["id"]
        _uid, key = _create_user(c, "conv-other-" + uuid.uuid4().hex[:6], role="viewer")
        H2 = {"Authorization": f"Bearer {key}"}
        assert not any(x["id"] == cid for x in c.get("/v1/chat/conversations", headers=H2).json())
        assert c.get(f"/v1/chat/conversations/{cid}", headers=H2).status_code == 404
        assert c.delete(f"/v1/chat/conversations/{cid}", headers=H2).status_code == 404
        # 本人仍可取
        assert c.get(f"/v1/chat/conversations/{cid}", headers=AUTH).status_code == 200

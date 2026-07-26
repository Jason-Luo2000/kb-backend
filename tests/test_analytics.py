"""数据看板（analytics）集成测：chat 结果埋点 + overview/top-queries/users/models 聚合 + 门控。
LLM 用 monkeypatch 假造；需 MinIO+ES 栈。运行：.venv/bin/pytest tests/test_analytics.py -q"""
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
    return ("根据资料，答案是 42。[1]", "test-llm")


def _kb_with_doc(c):
    with open(SAMPLE, "rb") as f:
        data = f.read() + f"\n\n# an-marker {uuid.uuid4().hex}\n".encode()
    kid = c.post("/v1/kbs", headers=AUTH, json={"name": "an-" + uuid.uuid4().hex[:6]}).json()["id"]
    doc = c.post(f"/v1/kbs/{kid}/docs", headers=AUTH, files={"file": ("d.md", data, "text/markdown")}).json()["docId"]
    return kid, doc


def test_analytics_overview_and_breakdown(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.generate", _fake_generate)
    with TestClient(app) as c:
        kid, doc = _kb_with_doc(c)
        try:
            # 两次有答 + 一次无命中（极高阈值）
            for _ in range(2):
                c.post("/v1/chat", headers=AUTH, json={"query": "高频问题X", "knowledgeBaseIds": [kid]})
            c.post("/v1/chat", headers=AUTH,
                   json={"query": "高频问题X", "knowledgeBaseIds": [kid], "similarityThreshold": 999})
            ov = c.get("/v1/admin/analytics/overview?days=0", headers=AUTH).json()
            assert ov["chats"] >= 3
            assert ov["answered"] >= 2
            assert ov["no_result"] >= 1
            assert ov["success_rate"] is not None and 0 < ov["success_rate"] < 1
            assert ov["active_users"] >= 1
        finally:
            c.delete(f"/v1/files/{doc}", headers=AUTH)


def test_analytics_top_queries(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.generate", _fake_generate)
    with TestClient(app) as c:
        kid, doc = _kb_with_doc(c)
        try:
            q = "uniq-question-" + uuid.uuid4().hex[:6]
            c.post("/v1/chat", headers=AUTH, json={"query": q, "knowledgeBaseIds": [kid]})
            c.post("/v1/chat", headers=AUTH, json={"query": q, "knowledgeBaseIds": [kid]})
            top = c.get("/v1/admin/analytics/top-queries?days=0", headers=AUTH).json()
            row = next((t for t in top if t["query"] == q), None)
            assert row and row["count"] >= 2
        finally:
            c.delete(f"/v1/files/{doc}", headers=AUTH)


def test_analytics_models_and_users(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.generate", _fake_generate)
    with TestClient(app) as c:
        kid, doc = _kb_with_doc(c)
        try:
            c.post("/v1/chat", headers=AUTH, json={"query": "m-q", "knowledgeBaseIds": [kid]})
            models = c.get("/v1/admin/analytics/models?days=0", headers=AUTH).json()
            assert any(m["model"] == "test-llm" and m["type"] == "llm" for m in models)
            us = c.get("/v1/admin/analytics/users?days=0", headers=AUTH).json()
            assert any(u["chats"] >= 1 for u in us)
        finally:
            c.delete(f"/v1/files/{doc}", headers=AUTH)


def test_analytics_requires_admin():
    with TestClient(app) as c:
        from tests.test_members import _create_user  # 复用建成员

        _uid, key = _create_user(c, "an-viewer-" + uuid.uuid4().hex[:6], role="viewer")
        assert c.get("/v1/admin/analytics/overview", headers={"Authorization": f"Bearer {key}"}).status_code == 403


def test_user_chats_records_answer(monkeypatch):
    """管理员可查看某用户的问答记录（含答案文本）。"""
    monkeypatch.setattr("app.adapters.llm.generate", _fake_generate)
    with TestClient(app) as c:
        kid, doc = _kb_with_doc(c)
        me = c.get("/v1/me", headers=AUTH).json()
        try:
            q = "history-q-" + uuid.uuid4().hex[:6]
            c.post("/v1/chat", headers=AUTH, json={"query": q, "knowledgeBaseIds": [kid]})
            chats = c.get(f"/v1/admin/analytics/users/{me['user_id']}/chats?days=0", headers=AUTH).json()
            row = next((r for r in chats if r["query"] == q), None)
            assert row and row["answer"] == "根据资料，答案是 42。[1]"
            assert row["outcome"] == "answered"
        finally:
            c.delete(f"/v1/files/{doc}", headers=AUTH)

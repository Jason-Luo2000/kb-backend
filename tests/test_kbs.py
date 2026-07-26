"""知识库批量管理：DELETE /v1/kbs/{id}（tenant admin；CASCADE 链接/授权；文件保留）。
运行：.venv/bin/pytest tests/test_kbs.py -q"""
import uuid

from starlette.testclient import TestClient

from app.main import app

OWNER = {"Authorization": "Bearer kb_dev_api_key"}


def _create_kb(c, name):
    return c.post("/v1/kbs", headers=OWNER, json={"name": name}).json()["id"]


def _create_user(c, external_id, role="viewer"):
    return c.post("/v1/admin/users", headers=OWNER, json={"externalId": external_id, "role": role}).json()["apiKey"]


def test_delete_kb_owner_ok():
    with TestClient(app) as c:
        kid = _create_kb(c, "del-" + uuid.uuid4().hex[:6])
        assert any(k["id"] == kid for k in c.get("/v1/kbs", headers=OWNER).json())
        assert c.delete(f"/v1/kbs/{kid}", headers=OWNER).status_code == 200
        assert not any(k["id"] == kid for k in c.get("/v1/kbs", headers=OWNER).json())


def test_delete_kb_forbidden_for_viewer():
    with TestClient(app) as c:
        kid = _create_kb(c, "forbid-" + uuid.uuid4().hex[:6])
        key = _create_user(c, "ku-" + uuid.uuid4().hex[:6], role="viewer")
        assert c.delete(f"/v1/kbs/{kid}", headers={"Authorization": f"Bearer {key}"}).status_code == 403
        # 仍在
        assert any(k["id"] == kid for k in c.get("/v1/kbs", headers=OWNER).json())


def test_delete_kb_404_cross_tenant():
    with TestClient(app) as c:
        assert c.delete(f"/v1/kbs/{uuid.uuid4()}", headers=OWNER).status_code == 404

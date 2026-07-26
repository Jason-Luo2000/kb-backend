"""成员管理（/v1/admin/users*）集成测。owner 建成员/授权；新成员以 viewer 身份验证可见性。
运行：.venv/bin/pytest tests/test_members.py -q（DB-backed，同 test_models）"""
import uuid

from starlette.testclient import TestClient

from app.main import app

OWNER = {"Authorization": "Bearer kb_dev_api_key"}


def _create_user(c, external_id, role="viewer", department=None):
    body = {"externalId": external_id, "role": role}
    if department:
        body["department"] = department
    r = c.post("/v1/admin/users", headers=OWNER, json=body).json()
    return r["userId"], r["apiKey"]


def _auth(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def test_list_users_requires_admin():
    with TestClient(app) as c:
        # 非 admin（新建 viewer）→ 403
        _uid, key = _create_user(c, "u-nonadmin-" + uuid.uuid4().hex[:6])
        assert c.get("/v1/admin/users", headers=_auth(key)).status_code == 403
        # owner → 200
        assert c.get("/v1/admin/users", headers=OWNER).status_code == 200


def test_create_user_and_visibility():
    with TestClient(app) as c:
        # 建一个 KB（team 可见）+ 一个 viewer 成员
        kid = c.post("/v1/kbs", headers=OWNER, json={"name": "m-kb-" + uuid.uuid4().hex[:6], "visibility": "team"}).json()["id"]
        uid, key = _create_user(c, "u-vis-" + uuid.uuid4().hex[:6], department="研发")
        # viewer 默认看不到 team 库（无授权）
        assert c.get("/v1/kbs", headers=_auth(key)).json() == []
        # owner 给该成员授权该库
        assert c.post(f"/v1/admin/users/{uid}/kbs", headers=OWNER,
                      json={"kbIds": [kid], "role": "viewer"}).json()["granted"] == 1
        # 现 viewer 能看到该库
        assert any(k["id"] == kid for k in c.get("/v1/kbs", headers=_auth(key)).json())
        # owner 视角：该成员可见库含此库，source=授权，可撤销
        kbs = c.get(f"/v1/admin/users/{uid}/kbs", headers=OWNER).json()
        row = next(k for k in kbs if k["kbId"] == kid)
        assert row["source"] == "授权" and row["canRevoke"] is True


def test_role_derived_access_for_editor():
    """editor 自动看到 team/tenant 库（角色派生，不可撤销）。"""
    with TestClient(app) as c:
        kid = c.post("/v1/kbs", headers=OWNER, json={"name": "e-kb-" + uuid.uuid4().hex[:6], "visibility": "team"}).json()["id"]
        uid, _key = _create_user(c, "u-ed-" + uuid.uuid4().hex[:6], role="editor")
        kbs = c.get(f"/v1/admin/users/{uid}/kbs", headers=OWNER).json()
        row = next(k for k in kbs if k["kbId"] == kid)
        assert row["source"] == "角色/可见性" and row["canRevoke"] is False


def test_department_filter_and_update():
    with TestClient(app) as c:
        uid, _ = _create_user(c, "u-d1-" + uuid.uuid4().hex[:6], department="财务")
        # 按部门筛
        rows = c.get("/v1/admin/users?department=财务", headers=OWNER).json()
        assert any(r["userId"] == uid for r in rows)
        assert all(r["department"] == "财务" for r in rows)
        # departments 列表
        assert "财务" in c.get("/v1/admin/users/departments", headers=OWNER).json()
        # 改部门
        c.patch(f"/v1/admin/users/{uid}", headers=OWNER, json={"department": "法务", "name": "张三"})
        rows = c.get("/v1/admin/users", headers=OWNER).json()
        assert next(r for r in rows if r["userId"] == uid)["department"] == "法务"


def test_revoke_removes_access():
    with TestClient(app) as c:
        kid = c.post("/v1/kbs", headers=OWNER, json={"name": "r-kb-" + uuid.uuid4().hex[:6], "visibility": "me"}).json()["id"]
        uid, key = _create_user(c, "u-rv-" + uuid.uuid4().hex[:6])
        c.post(f"/v1/admin/users/{uid}/kbs", headers=OWNER, json={"kbIds": [kid]})
        assert c.get("/v1/kbs", headers=_auth(key)).json()  # 有
        # revoke（DELETE /v1/acl）
        c.request("DELETE", "/v1/acl", headers=OWNER, json={"kbId": kid, "userId": uid})
        assert c.get("/v1/kbs", headers=_auth(key)).json() == []  # 没了


def test_delete_user():
    with TestClient(app) as c:
        uid, key = _create_user(c, "u-del-" + uuid.uuid4().hex[:6])
        assert c.delete(f"/v1/admin/users/{uid}", headers=OWNER).status_code == 200
        # 其 api_key 失效
        assert c.get("/v1/kbs", headers=_auth(key)).status_code == 401
        # 不能删自己
        me = c.get("/v1/me", headers=OWNER).json()
        assert c.delete(f"/v1/admin/users/{me["user_id"]}", headers=OWNER).status_code == 400

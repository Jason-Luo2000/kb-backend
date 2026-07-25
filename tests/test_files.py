"""F 阶段：个人文件库 + 库内文档管理 集成测（TestClient，需 MinIO+ES 栈）。
覆盖：drive 上传→列→attach(ingest)→ready→detach→硬删(ES 清)；KB 文档 remove/reparse/bulk。
用唯一内容避免 content_hash 去重命中历史样本。运行：.venv/bin/pytest tests/test_files.py -q"""
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
    """sample.md + 唯一标记 → 每次新 content_hash，避免去重命中。"""
    with open(SAMPLE, "rb") as f:
        base = f.read()
    return base + f"\n\n# test-marker {uuid.uuid4().hex}\n".encode()


def _create_kb(c, name):
    return c.post("/v1/kbs", headers=AUTH, json={"name": name}).json()["id"]


def test_drive_lifecycle_attach_detach_delete():
    with TestClient(app) as c:
        data = _unique_md()
        up = c.post("/v1/files", headers=AUTH, files={"file": ("life.md", data, "text/markdown")})
        assert up.status_code == 200
        fid = up.json()["fileId"]
        assert up.json()["status"] == "uploaded"  # 个人库未解析

        files = c.get("/v1/files", headers=AUTH).json()
        mine = next(f for f in files if f["fileId"] == fid)
        assert mine["kbCount"] == 0 and mine["status"] == "uploaded"

        kid = _create_kb(c, "f-life-" + fid[:6])
        att = c.post(f"/v1/files/{fid}/attach", headers=AUTH, json={"kbId": kid}).json()
        assert att["status"] == "ready"
        assert att["stats"]["chunks"] >= 1

        files = c.get("/v1/files", headers=AUTH).json()
        assert next(f for f in files if f["fileId"] == fid)["kbCount"] == 1

        assert c.post(f"/v1/files/{fid}/detach", headers=AUTH, json={"kbId": kid}).status_code == 200
        files = c.get("/v1/files", headers=AUTH).json()
        assert next(f for f in files if f["fileId"] == fid)["kbCount"] == 0

        assert c.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 200
        files = c.get("/v1/files", headers=AUTH).json()
        assert not any(f["fileId"] == fid for f in files)


def test_drive_upload_dedup():
    with TestClient(app) as c:
        data = _unique_md()
        r1 = c.post("/v1/files", headers=AUTH, files={"file": ("d.md", data, "text/markdown")}).json()
        r2 = c.post("/v1/files", headers=AUTH, files={"file": ("d.md", data, "text/markdown")}).json()
        assert r1["fileId"] == r2["fileId"] and r2["reused"] is True
        c.delete(f"/v1/files/{r1['fileId']}", headers=AUTH)


def test_kb_doc_remove_reparse_rename():
    with TestClient(app) as c:
        kid = _create_kb(c, "f-kb-" + uuid.uuid4().hex[:6])
        data = _unique_md()
        up = c.post(f"/v1/kbs/{kid}/docs", headers=AUTH,
                    files={"file": ("kb.md", data, "text/markdown")}).json()
        doc_id = up["docId"]
        assert up["status"] == "ready" and up["stats"]["version"] == 1

        rp = c.post(f"/v1/kbs/{kid}/docs/{doc_id}/reparse", headers=AUTH, json={}).json()
        assert rp["stats"]["version"] == 2

        assert c.patch(f"/v1/kbs/{kid}/docs/{doc_id}", headers=AUTH, json={"title": "renamed"}).status_code == 200
        docs = c.get(f"/v1/kbs/{kid}/docs", headers=AUTH).json()
        assert next(d for d in docs if d["docId"] == doc_id)["title"] == "renamed"

        assert c.delete(f"/v1/kbs/{kid}/docs/{doc_id}", headers=AUTH).status_code == 200
        docs = c.get(f"/v1/kbs/{kid}/docs", headers=AUTH).json()
        assert not any(d["docId"] == doc_id for d in docs)

        c.delete(f"/v1/files/{doc_id}", headers=AUTH)  # 清理个人库


def test_file_delete_404_when_missing():
    with TestClient(app) as c:
        assert c.delete(f"/v1/files/{uuid.uuid4()}", headers=AUTH).status_code == 404

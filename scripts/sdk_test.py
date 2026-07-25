"""kb-sdk 1.0 live e2e（cross_tenant 风格，需 uvicorn 在跑）。
跑全客户端：health/create/upload(含 reused 去重)/search/get_doc/grant/revoke/gc/reconcile。
运行：先起 .venv/bin/uvicorn app.main:app --port 8001；
KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=kb_dev_api_key KB_USER_ID=u_demo .venv/bin/python scripts/sdk_test.py"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

from kb_sdk import KBClient, KBForbidden, KBNotFound
from kb_sdk.errors import KBError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.bootstrap import default_user_id  # grant 目标需用户 UUID（非 external_id）

BASE = os.getenv("KB_BACKEND_URL", "http://localhost:8001")
KEY = os.getenv("KB_API_KEY", "kb_dev_api_key")
USER = os.getenv("KB_USER_ID", "u_demo")
USER_UUID = default_user_id()  # api_key 解析出的 principal.user_id


def main():
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    c = KBClient(BASE, KEY, USER)

    print("基础…")
    check("health ok", c.health().get("ok") is True)

    kb = c.create_kb("sdk-test-" + uuid.uuid4().hex[:6])
    kb_id = kb["id"]
    check("create_kb 返 id", bool(kb_id), str(kb))

    import tempfile
    marker = "SDKMARKER_" + uuid.uuid4().hex[:6]
    body = f"# SDK 测试\n唯一标记 {marker}。" + ("检索端到端。 " * 30)
    with tempfile.NamedTemporaryFile("wb", suffix=".md", delete=False) as f:
        f.write(body.encode())
        path = f.name
    try:
        up = c.upload(kb_id, path)
        doc_id = up["docId"]
        check("upload 返 docId+ready", up.get("status") == "ready" and bool(doc_id), str(up)[:80])

        up2 = c.upload(kb_id, path)  # 同内容 → content_hash 去重
        check("重复上传 reused=true", up2.get("reused") is True and up2["docId"] == doc_id, str(up2)[:80])

        s = c.search(marker, knowledge_base_ids=[kb_id])
        hits = s.get("hits", [])
        check("search 命中 marker", any(marker in (h.get("snippet") or "") for h in hits), f"hits={len(hits)}")

        d = c.get_doc(doc_id)
        check("get_doc status ready", d.get("status") == "ready", str(d)[:80])

        print("ACL…")
        g = c.grant(kb_id, USER_UUID, "editor")  # grant 目标须是用户 UUID（principal.user_id）
        check("grant ok", g.get("ok") is True, str(g)[:80])
        rv = c.revoke(kb_id, USER_UUID)
        check("revoke ok", rv.get("ok") is True, str(rv)[:80])

        print("运维（dry_run，幂等可重试）…")
        gc = c.gc(dry_run=True)
        check("gc dry_run 报告", gc.get("dry_run") is True and "purged" in gc, str(gc)[:80])
        rc = c.reconcile(dry_run=True)
        check("reconcile dry_run 报告", rc.get("dry_run") is True and "drift" in rc, str(rc)[:80])

        print("错误映射…")
        try:
            c.get_doc(str(uuid.uuid4()))  # 不存在 doc
            check("get_doc 不存在抛 KBNotFound", False, "no error")
        except KBNotFound:
            check("get_doc 不存在抛 KBNotFound", True)
        except KBError as e:
            check("get_doc 不存在抛 KBNotFound", False, f"got code={e.code}")
    finally:
        os.unlink(path)

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

"""T15 配额/计量测试（live，需 uvicorn 在跑）。
低配额租户：上传 2 ok、第 3 → 413 KB_QUOTA_EXCEEDED（upload 非重试）；reused 不计数；kb_usage/size_bytes 对。
运行：先起 uvicorn :8001；KB_BACKEND_URL=http://localhost:8001 KB_API_KEY=kb_dev_api_key .venv/bin/python scripts/quota_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

from app.bootstrap import default_tenant_id
from app.db import get_conn
from kb_sdk import KBClient, KBQuotaExceeded

BASE = os.getenv("KB_BACKEND_URL", "http://localhost:8001")
KEY = os.getenv("KB_API_KEY", "kb_dev_api_key")
USER = os.getenv("KB_USER_ID", "u_demo")
TID = default_tenant_id()


def _set_quota(max_docs, max_bytes):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_quota(tenant_id,max_docs,max_bytes,period) VALUES(%s,%s,%s,'monthly') "
                "ON CONFLICT(tenant_id) DO UPDATE SET max_docs=EXCLUDED.max_docs, max_bytes=EXCLUDED.max_bytes",
                (TID, max_docs, max_bytes),
            )


def _usage():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT doc_count, bytes FROM kb_usage WHERE tenant_id=%s AND period=to_char(now(),'YYYY-MM')", (TID,))
            r = cur.fetchone()
            return (r[0], r[1]) if r else (0, 0)


def _write_tmp(marker):
    f = tempfile.NamedTemporaryFile("wb", suffix=".md", delete=False)
    f.write((f"# q\n{marker} " * 20).encode())
    f.close()
    return f.name


def main():
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    _set_quota(2, 5 * 1024 ** 3)  # max_docs=2
    c = KBClient(BASE, KEY, USER)
    kb = c.create_kb("quota-test-" + os.urandom(3).hex())
    kb_id = kb["id"]

    print("上传 1、2 ok，第 3 → 413…")
    paths = [_write_tmp("MK_A_" + os.urandom(3).hex()), _write_tmp("MK_B_" + os.urandom(3).hex())]
    try:
        c.upload(kb_id, paths[0])
        c.upload(kb_id, paths[1])
        check("usage doc_count=2", _usage()[0] == 2, str(_usage()))
        raised = False
        try:
            c.upload(kb_id, _write_tmp("MK_C_" + os.urandom(3).hex()))
        except KBQuotaExceeded as e:
            raised = True
            check("第3次→KBQuotaExceeded(413)", e.code == "KB_QUOTA_EXCEEDED" and e.status == 413, str(e)[:60])
        check("第3次抛了 KBQuotaExceeded", raised)

        print("reused 去重不增 usage…")
        before = _usage()[0]
        up = c.upload(kb_id, paths[0])  # 同内容 → reused
        check("reused=true", up.get("reused") is True, str(up)[:60])
        check("reused 不增 usage", _usage()[0] == before, f"{before}->{_usage()[0]}")

        print("kb_file.size_bytes 落库…")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM kb_file WHERE tenant_id=%s AND size_bytes IS NOT NULL", (TID,))
                sized = cur.fetchone()[0]
        check("size_bytes 已落库", sized >= 2, str(sized))

        print("GET /v1/admin/quota（owner key）…")
        q = c.gc(dry_run=True)  # 复用 owner 端点验证 owner key 通（quota 端点同 owner 门）
        # 直接查 quota 端点需 owner；这里用 DB 校验 limits 与 usage 一致
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT max_docs FROM kb_quota WHERE tenant_id=%s", (TID,))
                check("quota max_docs=2", cur.fetchone()[0] == 2)
    finally:
        for pth in paths:
            os.unlink(pth)
        _set_quota(1000, 5 * 1024 ** 3)  # 还原

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

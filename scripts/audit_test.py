"""T15 审计哈希链测试（DB，gc_test 风格，需干净库）。
验：链完整 verify、字段篡改检测、prev_hash 篡改检测、全局桶、锚快照。
运行：.venv/bin/python scripts/audit_test.py"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import anchor_audit, append_audit, verify_audit_chain
from app.bootstrap import default_tenant_id, default_user_id
from app.db import get_conn
from app.middleware.auth import Principal, audit

TID = default_tenant_id()
UID = default_user_id()


def _row_ids(tenant):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM kb_audit_log WHERE tenant_id IS NOT DISTINCT FROM %s AND row_hash IS NOT NULL ORDER BY id",
                (tenant,),
            )
            return [r[0] for r in cur.fetchall()]


def main():
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    p = Principal(TID, UID)
    print("写审计行（audit() + append_audit 双路径 + 全局桶）…")
    audit("UPLOAD", principal=p, kb_ids=[str(uuid.uuid4())], result="ok", ua="t")
    audit("SEARCH", principal=p, query="q", hits=[str(uuid.uuid4())], ua="t")
    audit("SEC_VIOLATION", principal=p, hits=[str(uuid.uuid4())], result="dropped")
    with get_conn() as conn:
        append_audit(conn, tenant_id=TID, user_id=UID, action="gc_purge",
                     detail={"file_id": "x", "chunks": 1}, result="ok")
        append_audit(conn, tenant_id=None, user_id=UID, action="gc_prune_outbox",
                     detail={"deleted": 0}, result="ok")

    print("verify 链完整…")
    v = verify_audit_chain(tenant_id=TID)
    check("verified=True", v["verified"] is True, str(v))
    check("recomputed_mismatches=0", v["recomputed_mismatches"] == 0, str(v))
    check("prev_hash_breaks=0", v["prev_hash_breaks"] == 0, str(v))
    check("rows>=4", v["rows"] >= 4, str(v))
    # 全局桶
    vg = verify_audit_chain(tenant_id=None)
    check("全局桶含 gc_prune_outbox", vg["rows"] >= 1 and vg["verified"] is True, str(vg))

    print("篡改字段（result）→ 检测 mismatch…")
    ids = _row_ids(TID)
    last_id = ids[-1]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE kb_audit_log SET result='tampered' WHERE id=%s", (last_id,))
    v2 = verify_audit_chain(tenant_id=TID)
    check("篡改字段→mismatch>=1", v2["recomputed_mismatches"] >= 1, str(v2))
    check("篡改后 verified=False", v2["verified"] is False, str(v2))

    print("篡改 prev_hash → 检测 break…")
    mid_id = ids[len(ids) // 2]  # 有前驱的中间行
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE kb_audit_log SET prev_hash=decode('deadbeef','hex') WHERE id=%s", (mid_id,))
    v3 = verify_audit_chain(tenant_id=TID)
    check("篡改 prev_hash→break>=1", v3["prev_hash_breaks"] >= 1, str(v3))

    print("锚快照…")
    # 重置 tamper 让锚反映一个干净链不好做；锚只快照当前 tail row_hash，不要求 verified
    a = anchor_audit(tenant_id=None)  # 全局桶小且未被篡改
    check("anchor root_hash 非空", a.get("root_hash"), str(a)[:80])
    check("anchor row_count>=1", a.get("row_count", 0) >= 1, str(a)[:80])

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

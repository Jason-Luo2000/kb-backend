"""配额计量回退（meter_delete）：ingest 后 delete → 净 0（删文件释放配额）。
DB-backed，同 test_models。运行：.venv/bin/pytest tests/test_quota.py -q"""
from app.bootstrap import default_tenant_id
from app.db import get_conn
from app.quota import get_usage, meter_delete, meter_ingest


def test_meter_delete_roundtrip_zero_net():
    """上传 +1 后删除 -1 → 用量净不变（删了就释放）。"""
    tid = default_tenant_id()
    u0 = get_usage(tid)["doc_count"]
    with get_conn() as conn:
        meter_ingest(conn, tid, 1234)   # +1
    assert get_usage(tid)["doc_count"] == u0 + 1
    with get_conn() as conn:
        meter_delete(conn, tid, 1234)   # -1
    assert get_usage(tid)["doc_count"] == u0  # 净 0


def test_meter_delete_clamp_no_negative():
    """连续 delete 不致负（GREATEST clamp）。"""
    tid = default_tenant_id()
    u0 = get_usage(tid)["doc_count"]
    with get_conn() as conn:
        meter_delete(conn, tid, 9999)
        meter_delete(conn, tid, 9999)
    assert get_usage(tid)["doc_count"] == max(u0 - 2, 0)  # 不为负

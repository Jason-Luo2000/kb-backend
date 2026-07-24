"""T14 ES↔PG 对账测试：直造 4 类漂移 → dry_run 报告 → apply 修复 → 幂等。
直接调模块（无需 HTTP 服务）。运行：.venv/bin/python scripts/reconcile_test.py
"""
import hashlib
import io
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.bootstrap import default_tenant_id, default_user_id
from app.config import settings
from app.db import get_conn
from app.es import INDEX, get_es
from app.ingest import pipeline
from app.storage import get_minio

TID = default_tenant_id()
UID = default_user_id()


def _doc(marker: str) -> bytes:
    return (
        f"# 对账测试文档 {marker}\n\n唯一标记 {marker}。"
        + "".join(f"\n\n## 章节 {i}\n" + ("ES PG 对账修复漂移，PG 权威 ES 派生。 " * 30) for i in range(1, 6))
    ).encode()


def _setup_file(file_id, kb_id, data, storage_key):
    get_minio().put_object(settings.minio_bucket, storage_key, io.BytesIO(data), len(data))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO kb_kb(id,tenant_id,name,owner_id) VALUES(%s,%s,%s,%s)", (kb_id, TID, f"rc-{kb_id[:8]}", UID))
            cur.execute(
                "INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,status,owner_user_id) VALUES(%s,%s,%s,'rc.md',%s,'text/markdown','parsing',%s)",
                (file_id, TID, storage_key, hashlib.sha256(data).hexdigest(), UID),
            )
            cur.execute("INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES(%s,%s,%s)", (file_id, kb_id, TID))


def _chunk_id(file_id, version):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM kb_chunk WHERE file_id=%s AND chunk_version=%s ORDER BY chunk_order LIMIT 1", (file_id, version))
            r = cur.fetchone()
            return str(r[0]) if r else None


def _chunk_ids(file_id, version, n):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM kb_chunk WHERE file_id=%s AND chunk_version=%s ORDER BY chunk_order LIMIT %s", (file_id, version, n))
            return [str(r[0]) for r in cur.fetchall()]


def _outbox_pending(file_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM kb_outbox WHERE aggregate_id=%s AND published_at IS NULL", (file_id,))
            return cur.fetchone()[0]


def _es_src(doc_id):
    src = get_es().get(index=INDEX, id=doc_id)["_source"]
    return src or None


def _set_es_avail(doc_id, avail):
    es = get_es()
    src = es.get(index=INDEX, id=doc_id)["_source"]
    src["available_int"] = avail
    es.index(index=INDEX, id=doc_id, document=src)


def _outbox_total(file_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM kb_outbox WHERE aggregate_id=%s", (file_id,))
            return cur.fetchone()[0]


def main():
    from app.indexing.reconcile import reconcile

    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    file_id = str(uuid.uuid4())
    kb_id = str(uuid.uuid4())
    sk = f"{file_id}/v1/raw"
    mv1 = "MARKER_V1_" + uuid.uuid4().hex[:6]
    mv2 = "MARKER_V2_" + uuid.uuid4().hex[:6]

    print("v1→v2 摄取（active=2，v1 退役）…")
    _setup_file(file_id, kb_id, _doc(mv1), sk)
    pipeline.ingest_file(file_id)
    get_minio().put_object(settings.minio_bucket, sk, io.BytesIO(_doc(mv2)), len(_doc(mv2)))
    pipeline.ingest_file(file_id)

    active2 = _chunk_ids(file_id, 2, 2)
    check("v2 至少 2 chunk 用于造漂移", len(active2) >= 2, f"got {len(active2)}")
    cid_missing, cid_avail = active2[0], active2[1]
    cid_retired = _chunk_id(file_id, 1)  # v1 退役 chunk → 改可见造 retired_leak
    orphan_id = str(uuid.uuid4())

    es = get_es()
    outbox_before = _outbox_total(file_id)

    print("造 4 类漂移…")
    es.delete(index=INDEX, id=cid_missing)  # MISSING
    _set_es_avail(cid_avail, 0)  # AVAIL_DRIFT
    es.index(index=INDEX, id=orphan_id, document={  # ORPHAN_TOTAL（PG 无此 id）
        "tenant_id_kwd": TID, "file_id_kwd": file_id, "doc_type_kwd": "chunk",
        "available_int": 1, "chunk_version_int": 2,
    })
    _set_es_avail(cid_retired, 1)  # RETIRED_LEAK（v1 退役却可见）

    print("dry_run：报告 4 类、不写…")
    dr = reconcile(TID, file_id, dry_run=True, repair=True, principal_user_id=UID)
    d = dr["drift"]
    check("dry_run 标记", dr["dry_run"] is True)
    check("报 missing≥1", d["missing"] >= 1, str(d))
    check("报 avail_drift≥1", d["avail_drift"] >= 1, str(d))
    check("报 orphan≥1", d["orphan"] >= 1, str(d))
    check("报 retired_leak≥1", d["retired_leak"] >= 1, str(d))
    check("dry_run 未写 outbox", _outbox_total(file_id) == outbox_before, str(_outbox_total(file_id)))
    check("dry_run 未修：missing 仍缺", _es_src(cid_missing) is None)
    check("dry_run 未修：orphan 仍在", _es_src(orphan_id) is not None)

    print("apply：修复…")
    ap = reconcile(TID, file_id, dry_run=False, repair=True, principal_user_id=UID)
    check("apply 非 dry_run", ap["dry_run"] is False)
    miss_src = _es_src(cid_missing)
    check("MISSING 已重发（有 content+vec+版本）",
          miss_src and miss_src.get("content_tks") and miss_src.get("q_vec_vec") is not None
          and miss_src.get("chunk_version_int") == 2 and miss_src.get("available_int") == 1,
          "src keys=" + str(sorted((miss_src or {}).keys())))
    check("AVAIL_DRIFT 已修", _es_src(cid_avail).get("available_int") == 1)
    check("ORPHAN 已删", _es_src(orphan_id) is None)
    check("RETIRED_LEAK 已隐藏", _es_src(cid_retired).get("available_int") == 0)
    check("outbox 修复事件已发布（无 pending）", _outbox_pending(file_id) == 0)

    print("幂等：再跑 apply 全 0…")
    ap2 = reconcile(TID, file_id, dry_run=False, repair=True, principal_user_id=UID)
    d2 = ap2["drift"]
    check("幂等 missing=0", d2["missing"] == 0, str(d2))
    check("幂等 avail_drift=0", d2["avail_drift"] == 0, str(d2))
    check("幂等 orphan=0", d2["orphan"] == 0, str(d2))
    check("幂等 retired_leak=0", d2["retired_leak"] == 0, str(d2))

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

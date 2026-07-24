"""T11 版本与一致性测试：v1→v2 重建 + 原子 flip + drain barrier + 版本栅栏。
直接调模块（无需 HTTP 服务）。运行：.venv/bin/python scripts/version_test.py
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
    body = (
        f"# 版本测试文档 {marker}\n\n"
        f"本段含唯一标记 {marker}，用于区分版本。"
        + "".join(
            f"\n\n## 章节 {i}\n" + ("路 A 检索总结文档并通过锚点回原文精读。系统采用双路召回。 " * 30)
            for i in range(1, 6)
        )
    ).encode()
    return body


def _setup_file(file_id: str, kb_id: str, data: bytes, storage_key: str):
    get_minio().put_object(settings.minio_bucket, storage_key, io.BytesIO(data), len(data))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO kb_kb(id,tenant_id,name,owner_id) VALUES(%s,%s,%s,%s)", (kb_id, TID, f"ver-{kb_id[:8]}", UID))
            cur.execute(
                "INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,status,owner_user_id) VALUES(%s,%s,%s,'v.md',%s,'text/markdown','parsing',%s)",
                (file_id, TID, storage_key, hashlib.sha256(data).hexdigest(), UID),
            )
            cur.execute("INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES(%s,%s,%s)", (file_id, kb_id, TID))


def _active_versions(file_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active_doc_version,active_chunk_version,active_summary_version,active_anchor_version,status FROM kb_file WHERE id=%s",
                (file_id,),
            )
            return cur.fetchone()


def _chunk_avail_by_version(file_id):
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT chunk_version, available, count(*) n FROM kb_chunk WHERE file_id=%s GROUP BY chunk_version, available ORDER BY chunk_version",
                (file_id,),
            )
            return cur.fetchall()


def _outbox_remaining(file_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM kb_outbox WHERE aggregate_id=%s AND published_at IS NULL", (file_id,))
            return cur.fetchone()[0]


def _search_snippets(query):
    from app.middleware.auth import Principal
    from app.retrieval.orchestrator import retrieve

    return " ".join(h["snippet"] for h in retrieve(query, Principal(TID, UID))["hits"])


def main():
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    file_id = str(uuid.uuid4())
    kb_id = str(uuid.uuid4())
    sk = f"{file_id}/v1/raw"
    marker_v1 = "MARKER_V1_" + uuid.uuid4().hex[:6]
    marker_v2 = "MARKER_V2_" + uuid.uuid4().hex[:6]

    print("v1 摄取（含 LLM 总结）…")
    _setup_file(file_id, kb_id, _doc(marker_v1), sk)
    st = pipeline.ingest_file(file_id)
    print(f"  v1: version={st['version']} chunks={st['chunks']} summaries={st['summaries']}")
    check("v1 target=1", st["version"] == 1, f"got {st['version']}")
    av = _active_versions(file_id)
    check("v1 四指针全=1 + ready", av == (1, 1, 1, 1, "ready"), str(av))
    check("v1 outbox 全 published", _outbox_remaining(file_id) == 0, str(_outbox_remaining(file_id)))
    check("v1 检索命中 v1 标记", marker_v1 in _search_snippets(marker_v1), "no v1 hit")

    print("v2 重建（替换内容后同 file_id 再 ingest）…")
    data2 = _doc(marker_v2)
    get_minio().put_object(settings.minio_bucket, sk, io.BytesIO(data2), len(data2))  # 覆盖原文
    st2 = pipeline.ingest_file(file_id)
    print(f"  v2: version={st2['version']} chunks={st2['chunks']} summaries={st2['summaries']}")
    check("v2 target=2", st2["version"] == 2, f"got {st2['version']}")
    av2 = _active_versions(file_id)
    check("v2 四指针同时=2（原子，无中间态）", av2 == (2, 2, 2, 2, "ready"), str(av2))
    check("v2 outbox 全 published", _outbox_remaining(file_id) == 0)
    # v2 可见、v1 不可见（PG available）
    avail = _chunk_avail_by_version(file_id)
    v1_avail = [r for r in avail if r["chunk_version"] == 1]
    v2_avail = [r for r in avail if r["chunk_version"] == 2]
    check("v1 chunk 全 available=0（旧版隐藏）", all(r["available"] == 0 for r in v1_avail), str(v1_avail))
    check("v2 chunk 全 available=1（新版可见）", all(r["available"] == 1 for r in v2_avail), str(v2_avail))
    check("v2 检索命中 v2 标记、不含 v1", marker_v2 in _search_snippets(marker_v2) and marker_v1 not in _search_snippets(marker_v1))

    print("drain barrier：relay 失败时不 flip…")
    import app.indexing.relay as relay

    orig_drain = relay.drain
    relay.drain = lambda *a, **k: {"published": 0, "failed": 0, "remaining": 99}  # 不发布
    file_id2 = str(uuid.uuid4())
    kb_id2 = str(uuid.uuid4())
    sk2 = f"{file_id2}/v1/raw"
    _setup_file(file_id2, kb_id2, _doc("MARKER_FAIL_" + uuid.uuid4().hex[:6]), sk2)
    flipped = False
    try:
        pipeline.ingest_file(file_id2)
        flipped = True
    except RuntimeError as e:
        check("drain barrier 触发 RuntimeError", "drain incomplete" in str(e), str(e)[:60])
    relay.drain = orig_drain  # 恢复
    check("drain 失败时未 flip", not flipped and _active_versions(file_id2) is not None and _active_versions(file_id2)[0] == 1)
    # 失败的 staging 应 available=0（不可见）
    fa = _chunk_avail_by_version(file_id2)
    check("失败 staging chunk 全 available=0", fa and all(r["available"] == 0 for r in fa), str(fa))

    print("版本栅栏：旧版本 ES doc 强制 available=1 仍被 post-verify 丢弃…")
    # 取一个 v1 chunk，把它的 ES available_int 改 1（模拟漂移），active 仍=2 → 检索应丢弃
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM kb_chunk WHERE file_id=%s AND chunk_version=1 LIMIT 1", (file_id,))
            rogue = str(cur.fetchone()[0])
    es = get_es()
    src = es.get(index=INDEX, id=rogue)["_source"]
    src["available_int"] = 1
    es.index(index=INDEX, id=rogue, document=src)
    es.indices.refresh(index=INDEX)
    # 检索 marker_v1（v1 内容）—— ES 会返回它（available=1）但 post-verify 版本栅栏应丢弃
    snip = _search_snippets(marker_v1)
    check("版本栅栏丢弃漂移旧版本", marker_v1 not in snip, "v1 leaked despite version fence")
    # 清理 rogue，避免污染后续
    src["available_int"] = 0
    es.index(index=INDEX, id=rogue, document=src)

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

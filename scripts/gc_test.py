"""T14 GC/purge 测试：v1→v2 产生旧版本孤儿 → dry_run 投影 → apply 清理 → ES/检索一致。
直接调模块（无需 HTTP 服务）。运行：.venv/bin/python scripts/gc_test.py
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
        f"# GC 测试文档 {marker}\n\n本段含唯一标记 {marker}，用于区分版本。"
        + "".join(
            f"\n\n## 章节 {i}\n" + ("版本级垃圾回收与 ES PG 对账，回收旧版本空间。 " * 30)
            for i in range(1, 6)
        )
    ).encode()


def _setup_file(file_id, kb_id, data, storage_key, tid=TID, uid=UID):
    get_minio().put_object(settings.minio_bucket, storage_key, io.BytesIO(data), len(data))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO kb_kb(id,tenant_id,name,owner_id) VALUES(%s,%s,%s,%s)", (kb_id, tid, f"gc-{kb_id[:8]}", uid))
            cur.execute(
                "INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,status,owner_user_id) VALUES(%s,%s,%s,'gc.md',%s,'text/markdown','parsing',%s)",
                (file_id, tid, storage_key, hashlib.sha256(data).hexdigest(), uid),
            )
            cur.execute("INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES(%s,%s,%s)", (file_id, kb_id, tid))


def _version_counts(file_id):
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            out = {}
            for tbl, col in (("kb_chunk", "chunk_version"), ("kb_summary_doc", "summary_version"),
                             ("kb_anchor", "anchor_version"), ("kb_version", "doc_version")):
                cur.execute(f"SELECT {col} v, count(*) n FROM {tbl} WHERE file_id=%s GROUP BY {col} ORDER BY v", (file_id,))
                out[tbl] = {r["v"]: r["n"] for r in cur.fetchall()}
            return out


def _ids_at_version(file_id, version):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM kb_chunk WHERE file_id=%s AND chunk_version=%s", (file_id, version))
            chunks = [str(r[0]) for r in cur.fetchall()]
            cur.execute("SELECT id FROM kb_summary_doc WHERE file_id=%s AND summary_version=%s", (file_id, version))
            sums = [str(r[0]) for r in cur.fetchall()]
            return chunks, sums


def _es_present(doc_id):
    return bool(get_es().get(index=INDEX, id=doc_id)["_source"])


def _search_snippets(query):
    from app.middleware.auth import Principal
    from app.retrieval.orchestrator import retrieve

    return " ".join(h["snippet"] for h in retrieve(query, Principal(TID, UID))["hits"])


def _outbox_remaining(file_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM kb_outbox WHERE aggregate_id=%s AND published_at IS NULL", (file_id,))
            return cur.fetchone()[0]


def _setup_second_tenant():
    tid2 = str(uuid.uuid4())
    uid2 = str(uuid.uuid4())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO kb_tenant(id,name) VALUES(%s,%s)", (tid2, f"t2-{tid2[:6]}"))
            cur.execute("INSERT INTO kb_user(id,external_id) VALUES(%s,%s)", (uid2, f"u2-{uid2[:6]}"))
            cur.execute("INSERT INTO kb_user_tenant(user_id,tenant_id,role) VALUES(%s,%s,'owner')", (uid2, tid2))
    return tid2, uid2


def _seed_synthetic_v1_summary(file_id, summary_id, target_chunk_id):
    """种一个 v1 合成 summary+anchor+ES doc，让 summary/anchor 的 purge 路径不依赖 LLM 可用性。"""
    from app.retrieval import simhash

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_summary_doc(id,file_id,tenant_id,summary_type,heading_path,content_md,"
                "summary_text,content_fingerprint,source_chunk_ids,coverage_ratio,summary_version) "
                "VALUES(%s,%s,%s,'section_summary','/合成','合成 v1 总结','合成 v1 总结',%s,%s,1.0,1)",
                (summary_id, file_id, TID, simhash.simhash_hex("合成 v1 总结"), [target_chunk_id]),
            )
            cur.execute(
                "INSERT INTO kb_anchor(id,summary_doc_id,file_id,section_path,target_chunk_id,anchor_version) "
                "VALUES(%s,%s,%s,'/合成',%s,1)",
                (str(uuid.uuid4()), summary_id, file_id, target_chunk_id),
            )
    get_es().index(index=INDEX, id=summary_id, document={  # v1 退役 ES doc（available=0）
        "content_tks": "合成 v1 总结", "q_vec_vec": [0.0] * settings.zhipu_embed_dim,
        "file_id_kwd": file_id, "tenant_id_kwd": TID, "doc_type_kwd": "summary",
        "is_summary_int": 1, "available_int": 0,
    })


def main():
    from app.indexing.gc import prune_outbox, purge_versions

    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    file_id = str(uuid.uuid4())
    kb_id = str(uuid.uuid4())
    sk = f"{file_id}/v1/raw"
    mv1, mv2 = "MARKER_V1_" + uuid.uuid4().hex[:6], "MARKER_V2_" + uuid.uuid4().hex[:6]

    print("v1/v2 摄取（造 v1 孤儿）…")
    _setup_file(file_id, kb_id, _doc(mv1), sk)
    pipeline.ingest_file(file_id)
    get_minio().put_object(settings.minio_bucket, sk, io.BytesIO(_doc(mv2)), len(_doc(mv2)))
    pipeline.ingest_file(file_id)
    v1c0, _ = _ids_at_version(file_id, 1)
    _seed_synthetic_v1_summary(file_id, str(uuid.uuid4()), v1c0[0])  # 确定 summary/anchor purge 路径必测
    v1_chunks, v1_sums = _ids_at_version(file_id, 1)
    v2_chunks, v2_sums = _ids_at_version(file_id, 2)
    check("v1 孤儿已存在", len(v1_chunks) > 0 and len(v1_sums) > 0, f"chunks={len(v1_chunks)} sums={len(v1_sums)}")

    print("dry_run：投影计数、不写…")
    dr = purge_versions(TID, file_id, dry_run=True, principal_user_id=UID)
    check("dry_run 标记", dr["dry_run"] is True)
    check("dry_run 投影 chunks>0", dr["purged"]["chunks"] > 0, str(dr["purged"]))
    after_dr = _version_counts(file_id)
    check("dry_run 未删 v1 PG 行", after_dr["kb_chunk"].get(1, 0) == len(v1_chunks), str(after_dr["kb_chunk"]))
    check("dry_run 未写 outbox delete", _outbox_remaining(file_id) == 0)

    print("apply：清 v1…")
    ap = purge_versions(TID, file_id, dry_run=False, principal_user_id=UID)
    check("apply 非 dry_run", ap["dry_run"] is False)
    vc = _version_counts(file_id)
    check("v1 chunk 全删", vc["kb_chunk"].get(1, 0) == 0, str(vc["kb_chunk"]))
    check("v1 summary 全删", vc["kb_summary_doc"].get(1, 0) == 0, str(vc["kb_summary_doc"]))
    check("v1 anchor 全删", vc["kb_anchor"].get(1, 0) == 0, str(vc["kb_anchor"]))
    check("v1 version 全删", vc["kb_version"].get(1, 0) == 0, str(vc["kb_version"]))
    check("v2 chunk 保留", vc["kb_chunk"].get(2, 0) == len(v2_chunks), str(vc["kb_chunk"]))
    check("v2 summary 保留", vc["kb_summary_doc"].get(2, 0) == len(v2_sums), str(vc["kb_summary_doc"]))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT active_chunk_version,status FROM kb_file WHERE id=%s", (file_id,))
            av = cur.fetchone()
    check("kb_file active 不变", av == (2, "ready"), str(av))
    check("v1 ES doc 全删", all(not _es_present(i) for i in v1_chunks + v1_sums))
    check("v2 ES doc 保留", all(_es_present(i) for i in v2_chunks + v2_sums))
    check("outbox delete 已发布", _outbox_remaining(file_id) == 0)
    check("检索 v2 仍命中", mv2 in _search_snippets(mv2), "v2 missing")
    check("检索 v1 仍 miss", mv1 not in _search_snippets(mv1), "v1 leaked")

    print("prune_outbox：删旧 published、保 pending…")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_outbox(aggregate_id,event_type,payload,status,published_at) "
                "VALUES (%s,'index','{}','published',now() - interval '30 days')",
                (file_id,),
            )
            cur.execute(
                "INSERT INTO kb_outbox(aggregate_id,event_type,payload,status) VALUES (%s,'index','{}','pending')",
                (file_id,),
            )
    pr = prune_outbox(retain_days=7, principal_user_id=UID)
    check("prune 删了 1 行（30 天前 published）", pr["deleted"] == 1, str(pr))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FILTER (WHERE published_at IS NULL),"
                " count(*) FILTER (WHERE published_at < now() - interval '7 days') "
                "FROM kb_outbox WHERE aggregate_id=%s",
                (file_id,),
            )
            pend, old = cur.fetchone()
    check("prune 后 pending 保留、旧 published 清零", pend == 1 and old == 0, f"pending={pend} old_published={old}")

    print("租户隔离：另一租户 v1 不被默认租户 GC 触碰…")
    tid2, uid2 = _setup_second_tenant()
    f2 = str(uuid.uuid4())
    kb2 = str(uuid.uuid4())
    sk2 = f"{f2}/v1/raw"
    m2v1, m2v2 = "MARKER_T2V1_" + uuid.uuid4().hex[:6], "MARKER_T2V2_" + uuid.uuid4().hex[:6]
    _setup_file(f2, kb2, _doc(m2v1), sk2, tid=tid2, uid=uid2)
    pipeline.ingest_file(f2)
    get_minio().put_object(settings.minio_bucket, sk2, io.BytesIO(_doc(m2v2)), len(_doc(m2v2)))
    pipeline.ingest_file(f2)
    t2_v1_before = _version_counts(f2)["kb_chunk"].get(1, 0)
    purge_versions(TID, None, dry_run=False, principal_user_id=UID)  # 默认租户全量 GC
    t2_v1_after = _version_counts(f2)["kb_chunk"].get(1, 0)
    check("默认租户 GC 不碰 T2 v1", t2_v1_after == t2_v1_before, f"{t2_v1_before}->{t2_v1_after}")

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

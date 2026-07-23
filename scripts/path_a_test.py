"""T10 路 A 完整测试：稳定锚 valid/relocated/stale 三态 + A6 重定位成功率 + 超时软截止。
直接调模块（无需 HTTP 服务，但需库已 bootstrap + MinIO/ES 内存模式）。
运行：.venv/bin/python scripts/path_a_test.py
"""
import hashlib
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import psycopg  # noqa: F401 （确保依赖在）

from app.adapters import parser
from app.bootstrap import default_tenant_id, default_user_id
from app.config import settings
from app.db import get_conn
from app.ingest import chunker, pipeline
from app.retrieval import anchor, path_a, simhash

SAMPLE = os.path.join(os.path.dirname(__file__), "sample.md")
TID = default_tenant_id()
UID = default_user_id()


def _ingest_sample(tag: str = "pat"):
    """建 kb + 上传 sample.md + 摄取，返回 (kb_id, file_id)。"""
    data = open(SAMPLE, "rb").read() if os.path.exists(SAMPLE) else None
    assert data, "sample.md 不存在，先跑 e2e_demo 生成"
    from app.storage import get_minio

    kb_id, file_id = str(uuid.uuid4()), str(uuid.uuid4())
    sk = f"{file_id}/v1/raw"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO kb_kb(id,tenant_id,name,owner_id) VALUES(%s,%s,%s,%s)", (kb_id, TID, f"{tag}-{uuid.uuid4().hex[:4]}", UID))
            get_minio().put_object(settings.minio_bucket, sk, __import__("io").BytesIO(data), len(data))
            cur.execute(
                "INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,status,owner_user_id) VALUES(%s,%s,%s,'sample.md',%s,'text/markdown','parsing',%s)",
                (file_id, TID, sk, hashlib.sha256(data).hexdigest(), UID),
            )
            cur.execute("INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES(%s,%s,%s)", (file_id, kb_id, TID))
    stat = pipeline.ingest_file(file_id)
    return kb_id, file_id, stat


def _anchors(file_id):
    """返回 [(summary_doc_id, fingerprint, source_chunk_ids)]。"""
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT a.summary_doc_id, a.fingerprint, s.source_chunk_ids
                   FROM kb_anchor a JOIN kb_summary_doc s ON s.id=a.summary_doc_id
                   WHERE a.file_id=%s""",
                (file_id,),
            )
            return [(str(r["summary_doc_id"]), r["fingerprint"], [str(x) for x in r["source_chunk_ids"]]) for r in cur.fetchall()]


def _rechunk(file_id, text, size, doc_version, different=False):
    """模拟文档重切分：删旧 chunk，按新 size 重切插入新 chunk（新 chunk_id）。
    different=True 时用无关文本 → 触发 stale。"""
    blocks = parser.parse_bytes(text.encode("utf-8"), "text/markdown", "x.md")
    raw = chunker.chunk_blocks(blocks, size=size)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kb_chunk WHERE file_id=%s", (file_id,))
            for c in raw:
                cid = pipeline._chunk_id(file_id, doc_version, c["chunk_order"])
                content = c["content"] if not different else ("无关内容" + c["content"][1:] if c["content"] else c["content"])
                if different:
                    # 彻底换文本，保证 simhash 远离原指纹
                    content = f"完全不同的段落 {c['chunk_order']} 天气美食旅游，与原文无任何重叠。"
                cur.execute(
                    """INSERT INTO kb_chunk(id,file_id,tenant_id,doc_version,chunk_order,content,content_ltks,
                       section_path,page_num,chunk_version,content_hash,simhash,available)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)""",
                    (
                        cid, file_id, TID, doc_version, c["chunk_order"], content, content,
                        c["section_path"], c["page"], doc_version,
                        hashlib.sha256(content.encode()).hexdigest(), simhash.to_signed(simhash.simhash(content)),
                    ),
                )


def main():
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    print("ingesting sample.md（含 LLM 总结，约 10–30s）…")
    _, file_id, stat = _ingest_sample()
    print(f"  ingest: chunks={stat['chunks']} summaries={stat['summaries']} coverage={stat['coverage']}")
    anchors_list = _anchors(file_id)
    n = len(anchors_list)
    check("摄取产生了锚点（summary>0）", n > 0, f"anchors={n}")
    if n == 0:
        print("\nFAILURES ❌ 无锚点（LLM 未返回总结，无法测）")
        sys.exit(1)

    # 0. 超时软截止（fresh chunks，summary 在）：PATH_A_TIMEOUT_MS=1 → completed<total + degraded 含 timeout
    from app.adapters import embedder
    from app.middleware.auth import Principal
    from app.retrieval.orchestrator import _allowed_file_ids

    p = Principal(tenant_id=TID, user_id=UID)
    fids, clearance = _allowed_file_ids(None, p)
    q_vec = embedder.embed("总结文档")
    orig_to = settings.path_a_timeout_ms
    settings.path_a_timeout_ms = 1
    try:
        res = path_a.search(q_vec, "总结文档", fids, TID, clearance)
        check(
            "超时软截止（completed<=total + 降级原因）",
            res["completed"] <= res["total"] and ("timeout" in res["degraded"] or res["completed"] < res["total"]),
            f"completed={res['completed']}/{res['total']} degraded={res['degraded']}",
        )
    finally:
        settings.path_a_timeout_ms = orig_to

    # 1. valid：源 chunk 仍在 → resolve 全 valid
    valid_cnt = sum(
        1 for sid, _fp, srcs in anchors_list
        if anchor.resolve(file_id, srcs, sid, "路 A 召回", TID).validity == "valid"
    )
    check("valid 态（源 chunk 在）", valid_cnt == n, f"{valid_cnt}/{n}")

    # 2. relocated（A6）：重切分（不同 size → chunk_id 全变）→ 应重定位
    original_text = open(SAMPLE, "rb").read().decode("utf-8")
    new_size = max(128, settings.chunk_token_num // 2)  # 不同边界
    _rechunk(file_id, original_text, new_size, doc_version=2)
    relocated_cnt = sum(
        1 for sid, _fp, srcs in anchors_list
        if anchor.resolve(file_id, srcs, sid, "路 A 召回", TID).validity == "relocated"
    )
    rate = relocated_cnt / n
    check("A6 重定位成功率 >90%", rate > 0.9, f"{relocated_cnt}/{n} = {rate:.0%}")

    # 3. stale：换成无关文本 → resolve 全 stale
    _rechunk(file_id, original_text, new_size, doc_version=3, different=True)
    stale_cnt = sum(
        1 for sid, _fp, srcs in anchors_list
        if anchor.resolve(file_id, srcs, sid, "路 A 召回", TID).validity == "stale"
    )
    check("stale 态（无关文本不命中）", stale_cnt == n, f"{stale_cnt}/{n}")

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

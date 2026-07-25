"""C 阶段：分块 factory 单测 + parser_config 解析（DB-backed，与 test_models 同条件）。
运行：.venv/bin/pytest tests/test_chunker_factory.py -q"""
import uuid

import pytest

from app.adapters.parser import Block
from app.bootstrap import default_tenant_id
from app.db import get_conn
from app.ingest import chunker, chunker_factory as cf
from psycopg.types.json import Json


def _txt(s: str, page=1, sec=None):
    return Block(page=page, text=s, section_path=sec)


# ============ factory 纯函数 ============
def test_naive_default_byte_identical():
    """factory naive 默认参数 == chunker.chunk_blocks（保 content_hash/T12 复用）。"""
    blocks = [
        Block(page=1, text="# H1", section_path="H1", block_type="title", level=1),
        Block(page=1, text="body1 line", section_path="H1"),
        Block(page=1, text="## H2", section_path="H2", block_type="title", level=2),
        Block(page=1, text="body2", section_path="H2"),
    ]
    a = [c["content"] for c in chunker.chunk_blocks(blocks, size=512)]
    b = [c["content"] for c in cf.chunk("naive", blocks, {"chunk_token_num": 512})]
    assert a == b == ["# H1\nbody1 line", "## H2\nbody2"]


def test_one_whole_doc_single_chunk():
    blocks = [_txt("para one"), _txt("para two"), _txt("para three")]
    out = cf.chunk("one", blocks, {})
    assert len(out) == 1
    assert "para one" in out[0]["content"] and "three" in out[0]["content"]


def test_delimiter_clean_sentence_boundaries():
    """delimiter 按句号/问号/感叹号切出干净句；naive 走 token 滑窗会切在字中间。"""
    blocks = [_txt("第一句。第二句！第三句？第四句")]
    out = cf.chunk("delimiter", blocks, {"chunk_token_num": 4})
    assert [c["content"] for c in out] == ["第一句", "第二句", "第三句", "第四句"]


def test_qa_pairs():
    blocks = [_txt("Q1: 问题一\nA1: 答案一\nQ2: 问题二\nA2: 答案二")]
    out = cf.chunk("qa", blocks, {})
    assert len(out) == 2
    assert "问题一" in out[0]["content"] and "问题二" in out[1]["content"]


def test_qa_falls_back_when_no_structure():
    blocks = [_txt("普通文本，没有问答结构。")]
    assert cf.chunk("qa", blocks, {}) == cf.chunk("naive", blocks, {})


def test_domain_methods_run():
    blocks = [_txt("第一条 内容。第二条 内容。")]
    for m in ("book", "laws", "manual", "paper", "resume", "presentation", "table", "email", "tag"):
        out = cf.chunk(m, blocks, {"chunk_token_num": 8})
        assert isinstance(out, list) and all("content" in c for c in out)


def test_knowledge_graph_falls_back():
    blocks = [_txt("some text here")]
    assert cf.chunk("knowledge_graph", blocks, {})  # 非空（回退 naive）


def test_unknown_method_falls_back_naive():
    blocks = [_txt("hello"), _txt("world")]
    assert cf.chunk("bogus_method", blocks, {}) == cf.chunk("naive", blocks, {})


def test_chunk_order_reset_sequential():
    blocks = [_txt("Q1: a\nA1: b\nQ2: c\nA2: d")]
    out = cf.chunk("qa", blocks, {})
    assert [c["chunk_order"] for c in out] == list(range(len(out)))


# ============ parser_config 解析（DB）============
def _mk_file(parser_type="naive", parser_config=None):
    fid = str(uuid.uuid4())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,size_bytes,
                   status,parser_type,parser_config)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)""",
                (fid, default_tenant_id(), f"{fid}/raw", "t.md", fid, "text/markdown", 10,
                 parser_type, Json(parser_config) if parser_config else None),
            )
    return fid


def _link(fid, kid):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES (%s,%s,%s)",
                (fid, kid, default_tenant_id()),
            )


def _mk_kb(parser_config=None):
    kid = str(uuid.uuid4())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_kb(id,tenant_id,name,parser_config) VALUES (%s,%s,%s,%s)""",
                (kid, default_tenant_id(), f"t-{kid[:8]}", Json(parser_config) if parser_config else None),
            )
    return kid


def _cleanup(fid, kid):
    from app.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kb_file WHERE id=%s", (fid,))
            cur.execute("DELETE FROM kb_kb WHERE id=%s", (kid,))


def test_resolve_file_config_overrides_kb():
    from app.ingest.pipeline import _resolve_parse_cfg

    kid = _mk_kb({"method": "one", "chunk_token_num": 256})
    fid = _mk_file(parser_type="qa", parser_config={"method": "qa"})
    _link(fid, kid)
    try:
        method, cfg = _resolve_parse_cfg(fid)
        assert method == "qa"  # file 级覆盖 KB
        assert cfg.get("chunk_token_num") is None  # file 级 cfg 不含 KB 的 256
    finally:
        _cleanup(fid, kid)


def test_resolve_kb_fallback_when_file_has_none():
    from app.ingest.pipeline import _resolve_parse_cfg

    kid = _mk_kb({"method": "one"})
    fid = _mk_file(parser_type="naive", parser_config=None)  # 无 file 级配置
    _link(fid, kid)
    try:
        method, _ = _resolve_parse_cfg(fid)
        assert method == "one"  # 回退到 KB 配置
    finally:
        _cleanup(fid, kid)


def test_resolve_env_default_when_nothing_set():
    from app.ingest.pipeline import _resolve_parse_cfg

    kid = _mk_kb(parser_config=None)
    fid = _mk_file(parser_type="naive", parser_config=None)
    _link(fid, kid)
    try:
        method, cfg = _resolve_parse_cfg(fid)
        assert method == "naive"
        assert cfg == {}  # factory 用 env 默认
    finally:
        _cleanup(fid, kid)

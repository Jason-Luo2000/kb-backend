"""T13 chunker naive_merge 单测（纯函数，无需 DB）。
运行：.venv/bin/pytest tests/test_chunker.py -q"""
from app.adapters.parser import Block
from app.ingest import chunker


def _blocks(*specs):
    # specs: (text, block_type, page=1, section=None, bbox=None, level=None)
    out = []
    for sp in specs:
        text = sp[0]
        bt = sp[1]
        page = sp[2] if len(sp) > 2 else 1
        sec = sp[3] if len(sp) > 3 else None
        bbox = sp[4] if len(sp) > 4 else None
        lvl = sp[5] if len(sp) > 5 else None
        out.append(Block(page=page, text=text, section_path=sec, block_type=bt, bbox=bbox, level=lvl))
    return out


def test_title_is_boundary_no_cross_section_merge():
    """title 边界：两段不合并进同一 chunk。"""
    blocks = _blocks(
        ("sec1", "title", 1, "S1"),
        ("body one", "text", 1, "S1"),
        ("sec2", "title", 1, "S2"),
        ("body two", "text", 1, "S2"),
    )
    chunks = chunker.chunk_blocks(blocks, size=512)
    # 每段 title+body 一个 chunk
    assert len(chunks) == 2
    assert chunks[0]["section_path"] == "S1" and "body one" in chunks[0]["content"]
    assert chunks[1]["section_path"] == "S2" and "body two" in chunks[1]["content"]


def test_table_is_barrier_standalone_skip_summary():
    """table 屏障：独立 chunk，不与前后正文合并，skip_summary=True。"""
    blocks = _blocks(
        ("intro", "text", 1, "S"),
        ("a\tb\n1\t2", "table", 1, "S"),
        ("outro", "text", 1, "S"),
    )
    chunks = chunker.chunk_blocks(blocks, size=512)
    table_chunks = [c for c in chunks if c["skip_summary"]]
    assert len(table_chunks) == 1
    assert table_chunks[0]["content"] == "a\tb\n1\t2"
    # 表格不与正文合并：表格 chunk 内容只是表格
    assert "intro" not in table_chunks[0]["content"]


def test_chunk_order_monotonic_no_gaps():
    # 用 title 强制分段，得到多个 chunk 验证 order 单调无空洞
    blocks = _blocks(("t1", "title", 1, "s1"), ("t2", "title", 1, "s2"), ("t3", "title", 1, "s3"))
    chunks = chunker.chunk_blocks(blocks, size=512)
    assert [c["chunk_order"] for c in chunks] == [0, 1, 2]


def test_position_aggregated_from_bbox():
    """有 bbox 的块 → position=[{page,l,t,r,b}]；无 bbox → None。"""
    blocks = _blocks(
        ("hello", "text", 3, None, (1.0, 2.0, 3.0, 4.0)),
        ("world", "text", 3, None, (5.0, 6.0, 7.0, 8.0)),
    )
    chunks = chunker.chunk_blocks(blocks, size=512)
    assert len(chunks) == 1
    pos = chunks[0]["position"]
    assert pos == [
        {"page": 3, "l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0},
        {"page": 3, "l": 5.0, "t": 6.0, "r": 7.0, "b": 8.0},
    ]


def test_no_bbox_position_none():
    blocks = _blocks(("plain", "text"))  # 无 bbox（Office 文本）
    chunks = chunker.chunk_blocks(blocks, size=512)
    assert chunks[0]["position"] is None


def test_prose_merges_up_to_size():
    """多个小 prose 块合并到 token 上限内。"""
    blocks = _blocks(("one", "text"), ("two", "text"), ("three", "text"))
    chunks = chunker.chunk_blocks(blocks, size=512)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "one\ntwo\nthree"


def test_oversize_single_block_slides():
    """单块超 size → token 滑窗，多个 chunk。"""
    big = "word " * 2000  # 远超 512 token
    blocks = _blocks((big, "text"))
    chunks = chunker.chunk_blocks(blocks, size=64, overlap=0.1)
    assert len(chunks) > 1
    assert all(c["chunk_order"] == i for i, c in enumerate(chunks))

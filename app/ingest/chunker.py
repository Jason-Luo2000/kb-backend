"""naive_merge 分块（T13 重写）：版式感知合并。

规则（载重决策 D0.3/D0.4）：
- table/figure = 屏障：恒独立 chunk，不与正文合并（skip_summary=True，不进总结窗）。
- title = 边界：flush 暂存后作新段种子（向前与正文合并，避免标题独占小 chunk）。
- 其余 prose 块累加到 token 上限 size；超限则 flush 并 carry 尾部块作 overlap（仅 size-flush carry，
  边界 flush 不 carry 保段落干净）。
- 单块超 size → token 滑窗（沿用旧策略）。

输出每块：必备 content/page/section_path/chunk_order（pipeline 契约）+ 可选 position/skip_summary。
chunk_order 为 0 基单调计数（保 uuid5 chunk_id 稳定）。content 用 "\\n".join(块文本).strip()
→ MD 标题+正文重连与旧实现逐字一致（D0.2，保 content_hash/T12 复用）。"""
import dataclasses
import re

import tiktoken

from app.adapters.parser import Block
from app.config import settings

_enc = tiktoken.get_encoding("cl100k_base")

_PROSE = {"text", "title", "caption", "equation", "header", "footer"}
_BARRIER = {"table", "figure"}


def _apply_delimiter(blocks: list[Block], delimiter: str | None) -> list[Block]:
    """按分隔符字符集把 prose 块文本切成子块（保留 page/section/position/bbox）。
    delimiter=None/空 → 原样返回（默认路径，保 D0.2 字节一致）。barrier(表/图)块不切。"""
    if not delimiter:
        return blocks
    cls = "[" + re.escape(delimiter) + "]"
    out: list[Block] = []
    for b in blocks:
        if b.block_type in _BARRIER or not b.text or not b.text.strip():
            out.append(b)
            continue
        for piece in re.split(cls, b.text):
            piece = piece.strip()
            if piece:
                out.append(dataclasses.replace(b, text=piece))
    return out


def _tokens(text: str) -> list[int]:
    return _enc.encode(text)


def chunk_blocks(
    blocks: list[Block],
    size: int = settings.chunk_token_num,
    overlap: float = settings.chunk_overlap,
    delimiter: str | None = None,
) -> list[dict]:
    """返回 [{content, page, section_path, chunk_order, position, skip_summary}]。
    delimiter 非空时先把 prose 块按分隔符字符集切成子块再合并（默认 None→不切，保字节一致）。"""
    blocks = _apply_delimiter(blocks, delimiter)
    step = max(1, int(size * (1 - overlap)))
    overlap_tokens = max(0, int(size * overlap))
    pieces: list[dict] = []
    order = 0
    pending: list[Block] = []
    pending_toks = 0

    def ntok(b: Block) -> int:
        return len(_tokens(b.text))

    def pos_of(members: list[Block]):
        pos = [
            {"page": b.page, "l": b.bbox[0], "t": b.bbox[1], "r": b.bbox[2], "b": b.bbox[3]}
            for b in members
            if b.bbox
        ]
        return pos or None

    def add(content, page, section_path, position, skip_summary):
        nonlocal order
        if not content or not content.strip():
            return
        pieces.append(
            {
                "content": content.strip(),
                "page": page,
                "section_path": section_path,
                "chunk_order": order,
                "position": position,
                "skip_summary": skip_summary,
            }
        )
        order += 1

    def emit(members: list[Block]):
        texts = [b.text for b in members if b.text and b.text.strip()]
        if not texts:
            return
        add(
            "\n".join(texts).strip(),
            members[0].page,
            members[0].section_path,
            pos_of(members),
            any(b.block_type in _BARRIER for b in members),
        )

    def flush(carry: int):
        nonlocal pending, pending_toks
        if pending:
            emit(pending)
        if carry > 0 and pending:
            tail: list[Block] = []
            budget = carry
            for b in reversed(pending):
                if len(tail) >= len(pending):  # 不全带
                    break
                t = ntok(b)
                if t <= budget:
                    tail.insert(0, b)
                    budget -= t
                else:
                    break
            if 0 < len(tail) < len(pending):
                pending = tail
                pending_toks = sum(ntok(b) for b in tail)
                return
        pending, pending_toks = [], 0

    def emit_slides(b: Block):
        toks = _tokens(b.text)
        for start in range(0, len(toks), step):
            window = toks[start : start + size]
            add(_enc.decode(window).strip(), b.page, b.section_path, pos_of([b]), False)
            if start + size >= len(toks):
                break

    for b in blocks:
        if not b.text or not b.text.strip():
            continue
        t = ntok(b)
        if b.block_type in _BARRIER:  # 表/图：独立 chunk
            flush(0)
            emit([b])
            continue
        if b.block_type == "title":  # 标题：边界 + 作新段种子
            flush(0)
            pending = [b]
            pending_toks = t
            continue
        if t > size:  # 单块超 size：滑窗
            flush(0)
            emit_slides(b)
            continue
        if pending and pending_toks + t > size:
            flush(overlap_tokens)  # size-flush + overlap carry
        pending.append(b)
        pending_toks += t
    flush(0)
    return pieces

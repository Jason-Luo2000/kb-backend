"""naive 分块（移植 RAGflow rag/app/naive.py 思路：token 数 + overlap）。
每块带 page / section_path / chunk_order，确定性 chunk_id 在 pipeline 生成。"""
import tiktoken

from app.adapters.parser import Block
from app.config import settings

_enc = tiktoken.get_encoding("cl100k_base")


def _tokens(text: str) -> list[int]:
    return _enc.encode(text)


def chunk_blocks(
    blocks: list[Block],
    size: int = settings.chunk_token_num,
    overlap: float = settings.chunk_overlap,
) -> list[dict]:
    """返回 [{content, page, section_path, chunk_order}]。"""
    step = max(1, int(size * (1 - overlap)))
    pieces: list[dict] = []
    order = 0
    for b in blocks:
        toks = _tokens(b.text)
        if len(toks) <= size:
            pieces.append(
                {"content": b.text.strip(), "page": b.page, "section_path": b.section_path, "chunk_order": order}
            )
            order += 1
            continue
        for start in range(0, len(toks), step):
            window = toks[start : start + size]
            pieces.append(
                {
                    "content": _enc.decode(window).strip(),
                    "page": b.page,
                    "section_path": b.section_path,
                    "chunk_order": order,
                }
            )
            order += 1
            if start + size >= len(toks):
                break
    return pieces

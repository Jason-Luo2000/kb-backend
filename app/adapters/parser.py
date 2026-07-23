"""文档解析适配器：MVP 用 pdfplumber（带页码）+ 纯文本/Markdown（按标题分段）。
后期切 DeepDoc（版式识别 + bbox，见方案 §4.2）。"""
import io
from dataclasses import dataclass

import pdfplumber


@dataclass
class Block:
    page: int
    text: str
    section_path: str | None = None


def parse_bytes(data: bytes, mime: str | None, name: str) -> list[Block]:
    n = (name or "").lower()
    if mime == "application/pdf" or n.endswith(".pdf"):
        return _parse_pdf(data)
    return _parse_text(data.decode("utf-8", "ignore"))


def _parse_pdf(data: bytes) -> list[Block]:
    blocks: list[Block] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            txt = page.extract_text() or ""
            if txt.strip():
                blocks.append(Block(page=i, text=txt))
    return blocks


def _parse_text(text: str) -> list[Block]:
    """Markdown/纯文本：以 # 标题行切分，继承 section_path。"""
    blocks: list[Block] = []
    cur_section: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if buf:
            joined = "\n".join(buf).strip()
            if joined:
                blocks.append(Block(page=1, text=joined, section_path=cur_section))
            buf.clear()

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            flush()
            cur_section = s.lstrip("#").strip() or cur_section
        buf.append(line)
    flush()
    return blocks or [Block(page=1, text=text.strip())]

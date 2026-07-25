"""分块方法注册表（C，RAGFlow 风格）。

chunk(method, blocks, parser_config) -> list[chunk_dict]。

方法（对照 RAGFlow rag/app/）：
- naive：版式感知合并（chunker.chunk_blocks，支持 size/overlap/delimiter；默认逐字等同旧实现）
- one：整篇一块（RAGFlow one）
- delimiter：按可配分隔符先切再合并（= naive + cfg.delimiter，缺省 。！？；\\n）
- qa：问答对（Q:/A:、问:/答:、Question:），每对一块；无结构→回退 naive
- book/laws/manual/paper/resume/email/tag：naive + 领域分隔符预设（纯文本，无重依赖）
- presentation/table：naive（幻灯按页、表为屏障，naive 已天然处理）
- knowledge_graph：stub（defer；需实体抽取，回退 naive + warn）

输出契约同 chunker：[{content, page, section_path, chunk_order, position, skip_summary}]。
"""
import re

from app.config import settings
from app.ingest import chunker

# 领域预设分隔符（字符集）：在朴素合并前把 prose 切得更细，模拟领域边界
_DOMAIN_DELIM = {
    "book": "\n。；！？",
    "laws": "\n。；",
    "manual": "\n。；",
    "paper": "\n。；！？",
    "resume": "\n",
    "email": "\n",
    "tag": "\n,，;；",
}

DEFAULT_DELIMITER = "。！？；\n"

# 供前端列出可选方法（name/label/domain）
METHOD_INFO = [
    {"name": "naive", "label": "通用（版式感知合并）", "domain": False},
    {"name": "one", "label": "整篇一块", "domain": False},
    {"name": "delimiter", "label": "按分隔符切（自定义）", "domain": False},
    {"name": "qa", "label": "问答对（Q&A）", "domain": False},
    {"name": "book", "label": "书籍（章节）", "domain": True},
    {"name": "laws", "label": "法律法规（条款）", "domain": True},
    {"name": "manual", "label": "手册（步骤）", "domain": True},
    {"name": "paper", "label": "论文（分节）", "domain": True},
    {"name": "resume", "label": "简历（段落）", "domain": True},
    {"name": "presentation", "label": "演示文稿（按页）", "domain": True},
    {"name": "table", "label": "表格优先", "domain": True},
    {"name": "email", "label": "邮件", "domain": True},
    {"name": "tag", "label": "标签", "domain": True},
    {"name": "knowledge_graph", "label": "知识图谱（待定）", "domain": True},
]

METHOD_NAMES = [m["name"] for m in METHOD_INFO]

_QA_MARK = re.compile(r"(?:^|\n)\s*(?:Q\d*\s*[:：]|问\d*\s*[:：]|Question\d*\s*[:：])", re.I)


def _cfg_val(cfg: dict, key: str, default):
    v = cfg.get(key)
    return default if v is None else v


def _common(cfg: dict) -> tuple[int, float]:
    size = int(_cfg_val(cfg, "chunk_token_num", settings.chunk_token_num))
    overlap = float(_cfg_val(cfg, "overlap", settings.chunk_overlap))
    return size, overlap


def _reorder(pieces: list[dict]) -> list[dict]:
    """复位 chunk_order 为 0 基单调（保 uuid5 chunk_id 稳定）。"""
    for i, p in enumerate(pieces):
        p["chunk_order"] = i
    return pieces


# ============ 方法实现 ============
def _naive(blocks, size, overlap, cfg):
    return chunker.chunk_blocks(blocks, size=size, overlap=overlap, delimiter=cfg.get("delimiter"))


def _delimiter(blocks, size, overlap, cfg):
    cfg = {**cfg, "delimiter": cfg.get("delimiter") or DEFAULT_DELIMITER}
    return _naive(blocks, size, overlap, cfg)


def _one(blocks, size, overlap, cfg):
    texts = [b.text for b in blocks if b.text and b.text.strip()]
    if not texts:
        return []
    return [{
        "content": "\n".join(texts).strip(),
        "page": blocks[0].page if blocks else 1,
        "section_path": None,
        "chunk_order": 0,
        "position": None,
        "skip_summary": False,
    }]


def _domain(delimiter: str):
    def fn(blocks, size, overlap, cfg):
        return chunker.chunk_blocks(blocks, size=size, overlap=overlap, delimiter=delimiter)
    return fn


def _qa(blocks, size, overlap, cfg):
    text = "\n".join(b.text for b in blocks if b.text and b.text.strip())
    if not text.strip():
        return []
    marks = [m.start() for m in _QA_MARK.finditer(text)]
    if len(marks) < 2:  # 无问答结构 → 回退 naive
        return chunker.chunk_blocks(blocks, size=size, overlap=overlap)
    page0 = blocks[0].page if blocks else 1
    step = max(1, int(size * (1 - overlap)))
    out: list[dict] = []
    for i, start in enumerate(marks):
        end = marks[i + 1] if i + 1 < len(marks) else len(text)
        seg = text[start:end].strip()
        if not seg:
            continue
        toks = chunker._tokens(seg)
        pieces = [seg] if len(toks) <= size or size <= 0 else [
            chunker._enc.decode(toks[s:s + size]).strip() for s in range(0, len(toks), step)
        ]
        for p in pieces:
            if p.strip():
                out.append({"content": p.strip(), "page": page0, "section_path": None,
                            "chunk_order": 0, "position": None, "skip_summary": False})
    return out or chunker.chunk_blocks(blocks, size=size, overlap=overlap)


def _kg_stub(blocks, size, overlap, cfg):
    print("[chunker] knowledge_graph 暂未实现（需实体抽取），回退 naive")
    return chunker.chunk_blocks(blocks, size=size, overlap=overlap)


METHODS = {
    "naive": _naive,
    "one": _one,
    "delimiter": _delimiter,
    "qa": _qa,
    "presentation": _naive,
    "table": _naive,
    "knowledge_graph": _kg_stub,
}
for _name, _delim in _DOMAIN_DELIM.items():
    METHODS[_name] = _domain(_delim)


def chunk(method: str | None, blocks, parser_config: dict | None = None) -> list[dict]:
    """按方法名分派。未知方法回退 naive。返回 chunk_dict 列表（chunk_order 已复位）。"""
    cfg = parser_config or {}
    size, overlap = _common(cfg)
    fn = METHODS.get(method or "naive", METHODS["naive"])
    return _reorder(fn(blocks, size, overlap, cfg))

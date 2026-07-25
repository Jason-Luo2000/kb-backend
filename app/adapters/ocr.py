"""OCR 适配器（T13 stub）：扫描件 PDF 文本提取。

默认关（settings.ocr_enabled）；开时 lazy import pytesseract + pdf2image。
本地无 tesseract/poppler 时 import 或调用失败 → warn-once + 返回 ""（页丢弃），
仿 embedder 的哈希伪向量兜底范式。real OCR 走 Dockerfile 后续：
  apt-get install -y tesseract-ocr tesseract-ocr-chi-sim poppler-utils
不在 pyproject 声明这些 lib（无系统二进制时 import 即失败）。
"""
from app.config import settings

_warned = False


def ocr_page(pdf_bytes: bytes, page_num: int) -> str:
    """对 PDF 第 page_num 页（1 基）做 OCR，返回文本。任何失败→warn-once+返回 ""。"""
    global _warned
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_bytes  # type: ignore

        imgs = convert_from_bytes(pdf_bytes, first_page=page_num, last_page=page_num, dpi=200)
        out = []
        for img in imgs:
            out.append(pytesseract.image_to_string(img, lang=settings.ocr_lang))
        return "\n".join(out)
    except Exception as e:  # noqa: BLE001
        if not _warned:
            print(
                f"[ocr] 不可用（{type(e).__name__}: {str(e)[:80]}），扫描页将被丢弃。"
                "启用需装 tesseract-ocr + poppler-utils（见 Dockerfile）并把 OCR_ENABLED=true。"
            )
            _warned = True
        return ""

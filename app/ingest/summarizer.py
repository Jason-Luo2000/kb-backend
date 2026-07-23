"""总结/目录文档生成（路 A 检索层）。prompt 来自方案附录 D.1：
JSON structured output + chunk_id 白名单约束 + self-check，防幻觉。"""
import json

import tiktoken

from app.adapters import llm
from app.config import settings

SUMMARY_SYSTEM = """你是文档结构化总结器。输入是一个文档窗口的切块列表（每块含 chunk_id 与文本）。
任务：产出若干总结条目，每条概括该窗口内的一个知识点/主题。
硬约束：
  1) source_chunk_ids 必须、且只能从下方【chunk_id 白名单】中选取——严禁编造不在列表中的 id；
  2) 每条总结须被所选 chunk 的文本完全支撑（可溯源）；
  3) 若窗口内容稀疏或无法可靠总结，返回空数组 []，不要硬凑。
只输出 JSON 数组，无任何解释：[{"summary_text":string,"heading_path":[string],"source_chunk_ids":[string],"coverage":"yes|no|uncertain"}]"""

_enc = tiktoken.get_encoding("cl100k_base")


def _extract_json(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.lower().startswith("json"):
            s = s[4:]
    a, b = s.find("["), s.rfind("]")
    return s[a : b + 1] if a != -1 and b != -1 else s


def summarize_window(window: list[dict]) -> list[dict]:
    whitelist = [c["chunk_id"] for c in window]
    block_list = "\n".join(f"[{c['chunk_id']}] {c['content']}" for c in window)
    prompt = f"【chunk_id 白名单】{json.dumps(whitelist, ensure_ascii=False)}\n\n【窗口文本】\n{block_list}"
    try:
        raw = llm.chat(prompt, system=SUMMARY_SYSTEM, max_tokens=4096)
        items = json.loads(_extract_json(raw))
    except Exception:
        return []
    valid: list[dict] = []
    for it in items:
        srcs = [s for s in it.get("source_chunk_ids", []) if s in whitelist]
        if srcs and it.get("coverage") in ("yes", "uncertain"):
            valid.append({**it, "source_chunk_ids": srcs})
    return valid


def summarize_file(
    chunks: list[dict],
    window_tokens: int = 8000,
    min_tokens: int = settings.min_tokens_to_summarize,
) -> list[dict]:
    """分窗总结；小文件（< min_tokens 或 < 5 块）跳过（路 A 不划算）。
    返回 [{summary_text, heading_path, source_chunk_ids, coverage}]。"""
    total = sum(len(_enc.encode(c["content"])) for c in chunks)
    if total < min_tokens or len(chunks) < 5:
        return []
    windows: list[list[dict]] = []
    cur: list[dict] = []
    cur_tokens = 0
    for c in chunks:
        t = len(_enc.encode(c["content"]))
        cur.append(c)
        cur_tokens += t
        if cur_tokens >= window_tokens:
            windows.append(cur)
            cur, cur_tokens = [], 0
    if cur:
        windows.append(cur)

    out: list[dict] = []
    for w in windows:
        out.extend(summarize_window(w))
    return out

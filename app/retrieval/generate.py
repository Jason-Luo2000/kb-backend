"""RAG 答案生成（/v1/chat 用）。

把检索命中（全文 content）拼成带 [n] 引用序号的上下文 → llm.generate → 组装 references。
答案里的 [n] 对应 references 顺序（按上下文条目顺序固定），调用方据此做可点引用。
"""
from app.adapters import llm

CONTEXT_CHUNKS = 8       # 上下文最多取多少条命中（防 prompt 超长）
PER_CHUNK_CHARS = 1500   # 单条命中纳入上下文的最大字符数

RAG_SYSTEM = """你是知识库问答助手。仅根据下方【参考资料】回答用户问题。
硬约束：
  1) 答案必须被资料支撑；在陈述事实的句末用 [1]/[2]… 标注来源序号（序号对应下方资料顺序）；
  2) 资料不足或无关时，如实说明"资料中未提及"，严禁编造；
  3) 输出 Markdown，简洁直接。"""

RAG_SYSTEM_NOCITE = """你是知识库问答助手。仅根据下方【参考资料】回答用户问题。
资料不足或无关时如实说明，不要编造。输出 Markdown，简洁直接。"""


def build_context(merged: list[dict], limit: int = CONTEXT_CHUNKS) -> tuple[str, list[dict]]:
    """前 limit 条命中 → '[n] content' 上下文 + references。
    references[i] = {index, docId, chunkId, page, snippet}，index 与上下文 [n] 一一对应。"""
    refs: list[dict] = []
    parts: list[str] = []
    for h in merged[:limit]:
        content = (h.get("content") or "").strip()
        if not content:
            continue
        if len(content) > PER_CHUNK_CHARS:
            content = content[:PER_CHUNK_CHARS] + "…"
        n = len(refs) + 1
        parts.append(f"[{n}] {content}")
        refs.append({
            "index": n,
            "docId": str(h.get("file_id", "")),
            "chunkId": str(h.get("chunk_id", "")),
            "page": h.get("page"),
            "snippet": content[:300],
        })
    return "\n\n".join(parts), refs


def generate_answer(
    query: str,
    merged: list[dict],
    *,
    model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    system_prompt: str | None = None,
    tenant_id: str | None = None,
    cite: bool = True,
    history: list[dict] | None = None,
) -> dict:
    """返回 {answer, references, model, error}。
    无命中→固定提示；LLM 未配置/失败→answer=None + error（端点降级）；model_id 无效→抛 RuntimeError。
    history：多轮对话上文 [{role:'user'|'assistant', content}]，拼进 prompt 让 LLM 理解指代。"""
    context, refs = build_context(merged)
    if not context:
        return {"answer": "未检索到相关资料，无法回答。", "references": [], "model": None, "error": None}

    system = system_prompt or (RAG_SYSTEM if cite else RAG_SYSTEM_NOCITE)
    hist_block = ""
    if history:
        turns = [h for h in history if h.get("content")][-8:]  # 最近 8 轮，防 prompt 过长
        if turns:
            hist_block = "【对话历史】\n" + "".join(
                f"{'用户' if h.get('role') == 'user' else '助手'}：{h['content']}\n" for h in turns
            ) + "\n结合对话历史理解当前问题（如「它/这个/接着说」等指代）。\n\n"
            if not system_prompt:
                system = system.rstrip() + "\n若有对话历史，需结合上文理解当前问题的指代。"
    prompt = f"{hist_block}【参考资料】\n{context}\n\n【当前问题】\n{query}"
    try:
        text, model = llm.generate(
            prompt, system, model_id=model_id, temperature=temperature,
            max_tokens=max_tokens, tenant_id=tenant_id,
        )
    except RuntimeError as e:
        if str(e) == "KB_MODEL_NOT_FOUND":
            raise  # 端点转 400
        return {"answer": None, "references": refs, "model": None, "error": f"LLM 未配置或不可用：{e}"}
    except Exception as e:  # noqa: BLE001  网络/上游错误 → 降级，不 500
        return {"answer": None, "references": refs, "model": None,
                "error": f"生成失败：{type(e).__name__}: {str(e)[:120]}"}
    return {"answer": text, "references": refs if cite else [], "model": model, "error": None}

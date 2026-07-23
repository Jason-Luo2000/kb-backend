"""引用溯源。MVP：返回结构化 references（chunk 级 page），答案标注交 LLM prompt；
中期接句级 insert_citations（按句 vs 源 chunk 相似度插 [doc:page]，红队 §D.3）。"""


def build_citation(answer: str, hits: list[dict]) -> dict:
    refs = []
    for h in hits:
        refs.append(
            {
                "docId": h.get("file_id") or h.get("docId"),
                "chunkId": h.get("chunk_id") or h.get("chunkId"),
                "page": h.get("page"),
                "snippet": (h.get("content") or h.get("snippet", ""))[:120],
            }
        )
    return {"answer": answer, "references": refs}

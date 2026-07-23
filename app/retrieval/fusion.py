"""RRF 融合 + 去重（方案 §4.0/§4.3：强制 RRF 在去重之前，每 hit 单一 RRF 分数）。"""
def rrf_merge(path_a: list[dict], path_b: list[dict], k: int = 60) -> list[dict]:
    buckets: dict[str, dict] = {}

    def add(hits: list[dict], tag: str) -> None:
        seen: set[str] = set()
        for rank, h in enumerate(hits):
            cid = h["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            weight = float(h.get("weight", 1.0))  # T10：路 A 低 coverage 命中降权
            b = buckets.setdefault(cid, {"hit": h, "score": 0.0, "paths": set()})
            b["score"] += weight / (k + rank)
            b["paths"].add(tag)

    if path_a:
        add(path_a, "a")
    if path_b:
        add(path_b, "b")

    out = []
    for b in sorted(buckets.values(), key=lambda x: x["score"], reverse=True):
        h = dict(b["hit"])
        h["score"] = b["score"]
        h["path"] = "".join(sorted(b["paths"]))  # a | b | ab
        out.append(h)
    return out

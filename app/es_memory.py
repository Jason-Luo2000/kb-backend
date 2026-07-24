"""内存版 ES 模拟（仅用于无容器环境验证业务逻辑；生产用真 ES）。
实现 ingest/retrieval 用到的子集：indices.exists/create/refresh、index、get、search（简化 DSL）。
search 解析 bool.filter(term/terms) + should.match + knn，做 token 重叠(BM25 近似) + cosine。"""
import math
import re
import threading

_LOCK = threading.Lock()


def _tok(s: str) -> list[str]:
    return [w for w in re.split(r"[\s,，。.;；:：!！?？()（）\[\]]+", str(s).lower()) if w]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _bm25(query: str, text: str) -> float:
    qs, ts = set(_tok(query)), set(_tok(text))
    return (len(qs & ts) / len(qs)) if qs else 0.0


class _Indices:
    def __init__(self, store):
        self._s = store

    def exists(self, index):
        return index in self._s._indices

    def create(self, index, mappings=None):
        self._s._indices.add(index)

    def refresh(self, index):
        pass


class FakeES:
    def __init__(self):
        self._docs: dict[tuple[str, str], dict] = {}
        self._indices: set[str] = set()
        self.indices = _Indices(self)

    def index(self, index, id=None, document=None, body=None, **kw):
        src = document or body or {}
        with _LOCK:
            self._docs[(index, id)] = dict(src)
        return {"result": "created"}

    def delete(self, index, id=None, **kw):
        # T14：幂等删除（缺失 no-op，对齐真 ES 语义）；GC/对账经 outbox delete 事件调用
        with _LOCK:
            existed = (index, id) in self._docs
            self._docs.pop((index, id), None)
        return {"result": "deleted" if existed else "not_found"}

    def scan(self, index, source_fields=None):
        # T14：match_all 等价（对账发现 ES 孤儿用）。返回 [(id, source)]。
        with _LOCK:
            items = [(did, dict(src)) for (idx, did), src in self._docs.items() if idx == index]
        if source_fields:
            items = [(did, {k: src.get(k) for k in source_fields}) for did, src in items]
        return items

    def get(self, index, id, source_includes=None, **kw):
        src = dict(self._docs.get((index, id), {}))
        if source_includes:
            src = {k: src.get(k) for k in source_includes}
        return {"_source": src}

    def search(self, index, body=None, **kw):
        body = body or {}
        size = body.get("size", 10)
        q = body.get("query", {}) or {}
        knn = body.get("knn")
        boolq = q.get("bool", {}) if isinstance(q, dict) else {}

        filters: dict[str, object] = {}
        for f in boolq.get("filter", []):
            for fld, cond in f.items():
                if fld == "term":
                    filters.update(cond)
                elif fld == "terms":
                    for k, v in cond.items():
                        filters[k] = set(v)

        should_match = None
        for s in boolq.get("should", []):
            if "match" in s:
                for k, v in s["match"].items():
                    should_match = (k, v)

        qvec = knn.get("query_vector") if knn else None

        with _LOCK:
            items = list(self._docs.items())
        cands = []
        for (idx, did), src in items:
            if idx != index:
                continue
            ok = True
            for k, v in filters.items():
                if isinstance(v, set):
                    if src.get(k) not in v:
                        ok = False
                        break
                elif src.get(k) != v:
                    ok = False
                    break
            if not ok:
                continue
            score = 0.0
            if should_match:
                score += _bm25(should_match[1], src.get(should_match[0], "") or "")
            if qvec:
                dv = src.get("q_vec_vec")
                if dv:
                    score += _cos(qvec, dv)
            cands.append((score, did, src))
        cands.sort(key=lambda x: x[0], reverse=True)
        hits = [{"_id": did, "_score": sc, "_source": src} for sc, did, src in cands[:size]]
        return {"hits": {"hits": hits, "total": {"value": len(cands)}}}

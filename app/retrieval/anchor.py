"""AnchorResolver（T10 路 A 稳定锚）：锚点选择 + valid/relocated/stale 三态。

模型（避开「查询期选择 vs 摄取期指纹」循环）：
- 源 chunk 仍存在 → select_center 按 query 特征重叠选中心，直接读（valid）；
- 源 chunk 全被删（文档重切分）→ 用 anchor 存的 fingerprint（simhash）扫该 file 当前块，
  Hamming ≤ path_a_relocate_hamming 视为命中（relocated），幂等改写 target_chunk_id；
- 找不到 → stale（调用方降级路 B）。

评审 #4/#20（simhash 重定位，sha256 仅校验）、#9（锚点选择禁用 sim(q_vec,chunk_vec)，
改置信度/语义命中度——此处用 query↔chunk 文本特征重叠）。
"""
from dataclasses import dataclass

from app.config import settings
from app.db import get_conn
from app.retrieval import simhash


@dataclass
class ResolveResult:
    chunk_id: str | None
    validity: str  # valid | relocated | stale


def select_center(source_chunk_ids: list[str], query: str, tenant_id: str) -> str | None:
    """在「仍存在」的候选 chunk 中，按 query 特征重叠度选中心（平局取靠前）。"""
    if not source_chunk_ids:
        return None
    q_feats = set(simhash.features(query))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content FROM kb_chunk WHERE id = ANY(%s) AND tenant_id = %s AND available = 1",
                (source_chunk_ids, tenant_id),
            )
            rows = cur.fetchall()
    if not rows:
        return None
    best, best_score = None, -1.0
    for cid, content in rows:
        score = len(q_feats & set(simhash.features(content or ""))) if q_feats else 0.0
        if score > best_score:
            best, best_score = str(cid), score
    return best


def _load_fingerprint(summary_doc_id: str) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fingerprint FROM kb_anchor WHERE summary_doc_id = %s LIMIT 1",
                (summary_doc_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def _relocate(file_id: str, fingerprint_hex: str, tenant_id: str) -> str | None:
    """扫该 file 当前块，找 simhash Hamming ≤ 阈值且距离最小的块。"""
    thr = settings.path_a_relocate_hamming
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, simhash FROM kb_chunk WHERE file_id = %s AND tenant_id = %s AND available = 1",
                (file_id, tenant_id),
            )
            best, best_d = None, thr + 1
            for cid, sh in cur.fetchall():
                if sh is None:
                    continue
                d = simhash.hamming_hex(fingerprint_hex, sh)
                if d <= thr and d < best_d:
                    best, best_d = str(cid), d
            return best


def _persist_relocate(summary_doc_id: str, new_chunk_id: str) -> None:
    """幂等记录重定位（改写 target_chunk_id、标 relocated）。best-effort。"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE kb_anchor SET target_chunk_id = %s, validity = 'relocated'
                       WHERE summary_doc_id = %s""",
                    (new_chunk_id, summary_doc_id),
                )
    except Exception:
        pass


def resolve(
    file_id: str,
    source_chunk_ids: list[str],
    summary_doc_id: str,
    query: str,
    tenant_id: str,
) -> ResolveResult:
    """返回锚点解析结果（valid/relocated/stale）。"""
    # 1. 源 chunk 仍在 → 选中心直接读（块是当前真实原文，无需指纹校验）
    center = select_center(source_chunk_ids, query, tenant_id)
    if center:
        return ResolveResult(center, "valid")
    # 2. 源全删（重切分）→ fingerprint 重定位
    fp = _load_fingerprint(summary_doc_id)
    if not fp:
        return ResolveResult(None, "stale")
    relocated = _relocate(file_id, fp, tenant_id)
    if relocated:
        _persist_relocate(summary_doc_id, relocated)
        return ResolveResult(relocated, "relocated")
    return ResolveResult(None, "stale")

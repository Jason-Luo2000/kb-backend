"""审计哈希链（T15）：append-only 防篡改 + trust anchor 快照。

review #29 + 红队修正：哈希链只是相对完整性，必须锚定外部 trust anchor（WORM/签名/Merkle 公开），
否则篡改者可整段重写（等保2.0三级8.1.4.3）。本模块：
- append_audit：统一链式写入（per-tenant advisory lock 串行 + best-effort prev_hash），运行在调用方事务
- verify_audit_chain：重算 row_hash 检测字段篡改 + 检查 prev_hash 链路篡改
- anchor_audit：链快照（root_hash=链尾 row_hash，累积摘要）→ 外部发布 seam（published stub）

canonical 单一 helper（insert/verify 共用，防漂移）：
row_hash = sha256(prev_hex + "|" + json.dumps(payload, sort_keys, separators=(",",":"), ensure_ascii=False))
"""
import hashlib
import ipaddress
import json

from psycopg.types.json import Json

_CHAIN_LOCK_GLOBAL = "__audit_global__"


# ---------- canonical（insert/verify 共用，防漂移）----------
def _opt_str(v):
    return None if v is None else str(v)


def _arr(v):
    """UUID[] → sorted list[str] or None（顺序无关哈希）。"""
    if v is None:
        return None
    return sorted(str(x) for x in v)


def _canon_ip(ip):
    """INET 规范化：能解析 → str(canonical)，否则原 str（两端一致）。"""
    if ip is None:
        return None
    s = str(ip)
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return s


def _canon_payload(*, tenant_id, user_id, action, kb_ids, query_text,
                   hit_chunk_ids, result, request_id, ip, user_agent, detail):
    """row_hash 载荷。排除 created_at（时间戳精度破坏可复现）。"""
    return {
        "tenant_id": _opt_str(tenant_id),
        "user_id": _opt_str(user_id),
        "action": action,
        "kb_ids": _arr(kb_ids),
        "query_text": query_text,
        "hit_chunk_ids": _arr(hit_chunk_ids),
        "result": result,
        "request_id": request_id,
        "ip": _canon_ip(ip),
        "user_agent": user_agent,
        "detail": detail,
    }


def _row_hash(prev_hash_bytes: bytes | None, payload: dict) -> bytes:
    body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=False)
    prev_hex = prev_hash_bytes.hex() if prev_hash_bytes else ""
    return hashlib.sha256((prev_hex + "|" + body).encode("utf-8")).digest()


# ---------- 链式写入 ----------
def append_audit(conn, *, tenant_id, user_id, action, kb_ids=None, query_text=None,
                 hit_chunk_ids=None, result="ok", request_id=None, ip=None,
                 user_agent=None, detail=None) -> None:
    """链式审计写入，运行在传入 conn 的事务里（gc/reconcile 保原子；audit() 传自有 conn）。

    自开常规 cursor（避免调用方 dict_row 取列问题）。per-tenant advisory lock（xact 级）串行：
    拿到锁→读链尾 row_hash 作 prev_hash；锁未拿→prev_hash=NULL（best-effort 链重启，不阻塞）。
    tenant_id=None 走全局桶（gc_prune_outbox）。
    """
    lock_key = tenant_id if tenant_id is not None else _CHAIN_LOCK_GLOBAL
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (lock_key,))
        got = cur.fetchone()[0]
        prev_hash = None
        if got:
            cur.execute(
                "SELECT row_hash FROM kb_audit_log "
                "WHERE tenant_id IS NOT DISTINCT FROM %s AND row_hash IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (tenant_id,),
            )
            row = cur.fetchone()
            if row and row[0]:
                prev_hash = row[0]
        payload = _canon_payload(
            tenant_id=tenant_id, user_id=user_id, action=action, kb_ids=kb_ids,
            query_text=query_text, hit_chunk_ids=hit_chunk_ids, result=result,
            request_id=request_id, ip=ip, user_agent=user_agent, detail=detail,
        )
        row_hash = _row_hash(prev_hash, payload)
        cur.execute(
            """INSERT INTO kb_audit_log(tenant_id,user_id,action,kb_ids,query_text,hit_chunk_ids,
               result,request_id,ip,user_agent,detail,prev_hash,row_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tenant_id, user_id, action, kb_ids, query_text, hit_chunk_ids, result,
             request_id, ip, user_agent, Json(detail) if detail is not None else None,
             prev_hash, row_hash),
        )


# ---------- 验证 ----------
def verify_audit_chain(tenant_id: str | None = None) -> dict:
    """重算 row_hash 检测字段篡改 + 检查 prev_hash 链路篡改。

    recomputed_mismatches: 字段被改但 row_hash 没重算（字段篡改）。
    prev_hash_breaks: prev_hash 非空但≠上行 row_hash（链路篡改）。
    gaps: row_hash IS NULL 或 prev_hash IS NULL 非首行（链写失败/锁 miss 重启，非致命）。
    """
    from app.db import get_conn

    rows = mismatches = breaks = gaps = 0
    head_id = tail_id = None
    first_mismatch = first_break = None
    prev_row_hash = None
    chain_started = False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, tenant_id, user_id, action, kb_ids, query_text, hit_chunk_ids, "
                "result, request_id, ip, user_agent, detail, prev_hash, row_hash "
                "FROM kb_audit_log WHERE tenant_id IS NOT DISTINCT FROM %s ORDER BY id ASC",
                (tenant_id,),
            )
            for r in cur.fetchall():
                (rid, tid, uid, action, kb_ids, qt, hits, result, req_id, ip, ua,
                 detail, prev_hash, row_hash) = r
                rows += 1
                if head_id is None:
                    head_id = rid
                tail_id = rid
                if row_hash is None:
                    gaps += 1
                    prev_row_hash = None
                    continue
                payload = _canon_payload(
                    tenant_id=tid, user_id=uid, action=action, kb_ids=kb_ids,
                    query_text=qt, hit_chunk_ids=hits, result=result, request_id=req_id,
                    ip=ip, user_agent=ua, detail=detail,
                )
                if _row_hash(prev_hash, payload) != row_hash:
                    mismatches += 1
                    if first_mismatch is None:
                        first_mismatch = rid
                if prev_hash is None:
                    if chain_started:
                        gaps += 1  # 锁 miss 重启
                elif prev_row_hash is not None and prev_hash != prev_row_hash:
                    breaks += 1
                    if first_break is None:
                        first_break = rid
                prev_row_hash = row_hash
                chain_started = True
    return {
        "verified": mismatches == 0 and breaks == 0,
        "rows": rows,
        "recomputed_mismatches": mismatches,
        "prev_hash_breaks": breaks,
        "gaps": gaps,
        "head_id": head_id,
        "tail_id": tail_id,
        "first_mismatch_id": first_mismatch,
        "first_break_id": first_break,
    }


# ---------- trust anchor 快照 ----------
def anchor_audit(tenant_id: str | None = None) -> dict:
    """链快照：root_hash = 链尾 row_hash（累积摘要，已承诺全链）。外部发布（WORM/签名）defer。"""
    from app.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), min(id), max(id) FROM kb_audit_log "
                "WHERE tenant_id IS NOT DISTINCT FROM %s AND row_hash IS NOT NULL",
                (tenant_id,),
            )
            cnt, head_id, tail_id = cur.fetchone()
            if not cnt:
                return {"tenant_id": tenant_id, "row_count": 0, "head_id": None,
                        "tail_id": None, "root_hash": None, "published": False}
            cur.execute("SELECT row_hash FROM kb_audit_log WHERE id=%s", (tail_id,))
            root = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO kb_audit_anchor(tenant_id,head_id,tail_id,row_count,root_hash) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id,anchored_at,published",
                (tenant_id, head_id, tail_id, cnt, root),
            )
            aid, anchored_at, published = cur.fetchone()
    return {
        "tenant_id": tenant_id,
        "anchor_id": aid,
        "head_id": head_id,
        "tail_id": tail_id,
        "row_count": cnt,
        "root_hash": root.hex() if root else None,
        "anchored_at": str(anchored_at),
        "published": published,
    }

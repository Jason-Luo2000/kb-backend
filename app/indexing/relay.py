"""Outbox relay（T11，进程内同步实现）：消费 kb_outbox 幂等发布到 ES。

评审 #11：PG 权威、ES 派生。摄取把 ES 写建模为 outbox 事件，与 PG 元数据同事务写入；
relay 读 pending 事件发布到 ES，幂等（确定性 id upsert），失败记 attempts、超 max 标 failed。
drain barrier：ingest flip 前要求该 file 无 pending/failed 事件（确保 ES 已落库）。

后期换 Redis Streams 消费者组只需替换 drain 实现，业务代码（写 outbox）不变。
"""
import json

from app.config import settings
from app.db import get_conn
from app.es import INDEX, get_es


def _publish(event_type: str, payload) -> None:
    es = get_es()
    if event_type == "index":
        es.index(index=INDEX, id=payload["id"], document=payload["source"])
    elif event_type == "set_available":
        avail = payload["available"]
        for eid in payload["ids"]:
            src = es.get(index=INDEX, id=eid)["_source"]  # read-modify-write（FakeES/真 ES 通用）
            src["available_int"] = avail
            es.index(index=INDEX, id=eid, document=src)
    elif event_type == "delete":
        for eid in payload["ids"]:  # T14：GC/对账删 ES doc（幂等，缺失 no-op）
            es.delete(index=INDEX, id=eid)
    else:
        raise ValueError(f"unknown outbox event_type: {event_type}")


def drain(file_id: str | None = None) -> dict:
    """发布 pending outbox 事件到 ES。返回 {published, failed, remaining}。"""
    from psycopg.rows import dict_row

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT id, event_type, payload, attempts FROM kb_outbox
                   WHERE published_at IS NULL"""
                + (" AND aggregate_id=%s ORDER BY created_at" if file_id else " ORDER BY created_at"),
                ((file_id,) if file_id else ()),
            )
            rows = cur.fetchall()
            published = failed = 0
            for r in rows:
                payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
                try:
                    _publish(r["event_type"], payload)
                    cur.execute(
                        "UPDATE kb_outbox SET status='published', published_at=now() WHERE id=%s",
                        (r["id"],),
                    )
                    published += 1
                except Exception as e:  # noqa: BLE001
                    attempts = (r["attempts"] or 0) + 1
                    status = "failed" if attempts >= settings.outbox_max_attempts else "pending"
                    cur.execute(
                        "UPDATE kb_outbox SET attempts=%s, last_error=%s, status=%s WHERE id=%s",
                        (attempts, str(e)[:500], status, r["id"]),
                    )
                    failed += 1
    if published:
        try:
            get_es().indices.refresh(index=INDEX)
        except Exception:
            pass
    remaining = pending_count(file_id)
    return {"published": published, "failed": failed, "remaining": remaining}


def pending_count(file_id: str | None = None) -> int:
    """未发布（pending 或 failed）事件数——drain barrier 用。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM kb_outbox WHERE published_at IS NULL"
                + (" AND aggregate_id=%s" if file_id else ""),
                ((file_id,) if file_id else ()),
            )
            return cur.fetchone()[0]

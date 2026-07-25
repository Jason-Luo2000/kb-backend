"""文件级硬删（F）：整文件 PG CASCADE + MinIO 对象 + ES doc（经 outbox delete 同事务）。

对照 gc.purge_versions（版本级回收）；此处删全部版本，用于个人文件库 / 文档彻底删除。
ES doc id = kb_chunk.id / kb_summary_doc.id（ingest 经 outbox index 写入），故删 ES 用同款
outbox delete 事件（relay._publish 'delete' 分支，幂等缺失 no-op）。
"""
import json

from app.config import settings
from app.db import get_conn
from app.indexing.gc import DELETE_BATCH


def purge_file(tenant_id: str, file_id: str) -> tuple[bool, str | None]:
    """硬删整文件。返回 (deleted, error)。跨租户/不存在→(False, 'KB_FILE_NOT_FOUND')。"""
    from app.indexing import relay
    from app.storage import get_minio

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id, storage_key FROM kb_file WHERE id=%s", (file_id,))
            row = cur.fetchone()
            if not row or str(row[0]) != str(tenant_id):
                return False, "KB_FILE_NOT_FOUND"
            storage_key = row[1]
            # 收集 ES id（chunk + summary，所有版本）
            cur.execute("SELECT id FROM kb_chunk WHERE file_id=%s", (file_id,))
            chunk_ids = [str(r[0]) for r in cur.fetchall()]
            cur.execute("SELECT id FROM kb_summary_doc WHERE file_id=%s", (file_id,))
            summary_ids = [str(r[0]) for r in cur.fetchall()]
            es_ids = chunk_ids + summary_ids
            for i in range(0, len(es_ids), DELETE_BATCH):
                batch = es_ids[i : i + DELETE_BATCH]
                cur.execute(
                    "INSERT INTO kb_outbox(aggregate_id,event_type,payload) VALUES (%s,'delete',%s)",
                    (file_id, json.dumps({"ids": batch, "reason": "file_delete"})),
                )
            # 删 PG（CASCADE chunks/summaries/anchors/versions/kb_file_kb）
            cur.execute("DELETE FROM kb_file WHERE id=%s", (file_id,))
    # commit 后 drain（发 ES delete）+ MinIO 对象删（best-effort）
    relay.drain(file_id)
    try:
        get_minio().remove_object(settings.minio_bucket, storage_key)
    except Exception:  # noqa: BLE001
        pass
    return True, None

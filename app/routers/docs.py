"""文档：上传+摄取 / 状态 / 精读锚点。"""
import hashlib
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from psycopg.rows import dict_row

from app.config import settings
from app.db import get_conn
from app.ingest import pipeline
from app.middleware.auth import audit, limiter, verify_api_key
from app.retrieval import orchestrator
from app.storage import get_minio

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


@router.post("/kbs/{kb_id}/docs")
@limiter.limit("30/minute")
def upload_doc(kb_id: str, request: Request, file: UploadFile = File(...)):
    data = file.file.read()
    content_hash = hashlib.sha256(data).hexdigest()
    file_id = str(uuid.uuid4())
    storage_key = f"{file_id}/v1/raw"
    get_minio().put_object(settings.minio_bucket, storage_key, io.BytesIO(data), len(data))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_file(id,storage_key,name,content_hash,mime,status)
                   VALUES (%s,%s,%s,%s,%s,'parsing')""",
                (file_id, storage_key, file.filename, content_hash, file.content_type),
            )
            cur.execute("INSERT INTO kb_file_kb(file_id,kb_id) VALUES (%s,%s)", (file_id, kb_id))
    try:
        stat = pipeline.ingest_file(file_id)
        audit("UPLOAD", kb_ids=[kb_id], result="ok", ua=request.headers.get("user-agent"))
        return {"docId": file_id, "status": "ready", "stats": stat}
    except Exception as e:  # noqa: BLE001
        audit("UPLOAD", kb_ids=[kb_id], result="fail")
        raise HTTPException(status_code=500, detail=f"ingest failed: {e}") from e


@router.get("/docs/{doc_id}")
def get_doc(doc_id: str):
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id,name,status,page_count FROM kb_file WHERE id=%s", (doc_id,))
            f = cur.fetchone()
    if not f:
        raise HTTPException(status_code=404, detail="KB_DOC_NOT_FOUND")
    return {"docId": str(f["id"]), "title": f["name"], "status": f["status"], "pages": f["page_count"]}


@router.post("/read-anchor")
def read_anchor(body: dict):
    r = orchestrator.read_anchor(body["docId"], body["anchor"], body.get("before", 2), body.get("after", 4))
    if not r:
        raise HTTPException(status_code=404, detail="KB_ANCHOR_STALE")
    return r

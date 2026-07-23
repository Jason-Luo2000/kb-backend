"""文档：上传+摄取 / 状态 / 精读锚点。
T9：上传需 editor+ 且 kb ∈ allowed；read_anchor 与 /search 同级 ACL（红队越权修复）。"""
import hashlib
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from psycopg.rows import dict_row

from app.authz import can_write, resolve as resolve_authz
from app.config import settings
from app.db import get_conn
from app.ingest import pipeline
from app.middleware.auth import audit, get_principal, limiter, verify_api_key
from app.retrieval import orchestrator
from app.storage import get_minio

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


def _can_read_file(principal, file_id: str) -> bool:
    """file_id 是否在调用者租户的授权 kb 内（tenant + allowed 双重）。"""
    decision = resolve_authz(principal)
    if not decision.allowed_kb_ids:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM kb_file_kb fk
                   JOIN kb_kb k ON k.id = fk.kb_id
                   WHERE fk.file_id = %s AND k.tenant_id = %s AND fk.kb_id = ANY(%s)
                   LIMIT 1""",
                (file_id, principal.tenant_id, decision.allowed_kb_ids),
            )
            return cur.fetchone() is not None


@router.post("/kbs/{kb_id}/docs")
@limiter.limit("30/minute")
def upload_doc(kb_id: str, request: Request, file: UploadFile = File(...)):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    if kb_id not in decision.allowed_kb_ids or not can_write(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    data = file.file.read()
    content_hash = hashlib.sha256(data).hexdigest()
    file_id = str(uuid.uuid4())
    storage_key = f"{file_id}/v1/raw"
    get_minio().put_object(settings.minio_bucket, storage_key, io.BytesIO(data), len(data))
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,status,owner_user_id)
                       VALUES (%s,%s,%s,%s,%s,%s,'parsing',%s)""",
                    (
                        file_id,
                        principal.tenant_id,
                        storage_key,
                        file.filename,
                        content_hash,
                        file.content_type,
                        principal.user_id,
                    ),
                )
                cur.execute(
                    "INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES (%s,%s,%s)",
                    (file_id, kb_id, principal.tenant_id),
                )
        stat = pipeline.ingest_file(file_id)
        audit("UPLOAD", request, kb_ids=[kb_id], result="ok", ua=request.headers.get("user-agent"))
        return {"docId": file_id, "status": "ready", "stats": stat}
    except Exception as e:  # noqa: BLE001
        audit("UPLOAD", request, kb_ids=[kb_id], result="fail")
        raise HTTPException(status_code=500, detail=f"ingest failed: {e}") from e


@router.get("/docs/{doc_id}")
def get_doc(doc_id: str, request: Request):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT f.id, f.name, f.status, f.page_count
                   FROM kb_file f
                   WHERE f.id = %s AND f.tenant_id = %s
                     AND EXISTS (SELECT 1 FROM kb_file_kb fk
                                 WHERE fk.file_id = f.id AND fk.kb_id = ANY(%s))""",
                (doc_id, principal.tenant_id, decision.allowed_kb_ids or [str(uuid.UUID(int=0))]),
            )
            f = cur.fetchone()
    if not f:
        raise HTTPException(status_code=404, detail="KB_DOC_NOT_FOUND")
    return {"docId": str(f["id"]), "title": f["name"], "status": f["status"], "pages": f["page_count"]}


@router.post("/read-anchor")
def read_anchor(body: dict, request: Request):
    principal = get_principal(request)
    file_id = body["docId"]
    # ACL 闸门：file_id 必须在调用者租户的授权 kb 内（红队：与 /search 同级 ACL，防越权读原文窗口）
    if not _can_read_file(principal, file_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    r = orchestrator.read_anchor(file_id, body["anchor"], principal, body.get("before", 2), body.get("after", 4))
    if not r:
        raise HTTPException(status_code=404, detail="KB_ANCHOR_STALE")
    return r

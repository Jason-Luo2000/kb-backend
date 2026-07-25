"""个人文件库（F）：上传到个人空间（不入库）→ 列 / 改 / 删 → 挂到 KB 触发解析 → 解挂。

模型：kb_file 已是一等公民（owner_user_id + kb_file_kb 多对多 + 租户级 content_hash 去重）。
status='uploaded' = 在个人库未解析；attach 时若未解析则 ingest→'ready'，已解析则仅链接（chunks 跨库共享）。
硬删走 file_store.purge_file（PG CASCADE + MinIO + ES doc）。
"""
import hashlib
import io
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.authz import can_write, is_tenant_owner, resolve as resolve_authz
from app.db import get_conn
from app.ingest import pipeline
from app.indexing.file_store import purge_file
from app.middleware.auth import audit, get_principal, limiter, verify_api_key
from app.quota import check_quota, meter_ingest
from app.storage import get_minio
from app.config import settings

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


def _parse_cfg(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}


def _can_manage_file(principal, file_id: str) -> bool:
    """owner 自身 / 租户 owner / 文件所在可写 KB。"""
    if is_tenant_owner(principal):
        return True
    decision = resolve_authz(principal)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT owner_user_id FROM kb_file WHERE id=%s AND tenant_id=%s",
                (file_id, principal.tenant_id),
            )
            row = cur.fetchone()
            if not row:
                return False
            if row[0] and str(row[0]) == principal.user_id:
                return True
            cur.execute("SELECT kb_id FROM kb_file_kb WHERE file_id=%s", (file_id,))
            kb_ids = [str(r[0]) for r in cur.fetchall()]
    return any(can_write(decision, k) for k in kb_ids)


@router.post("/files")
@limiter.limit("30/minute")
def upload_to_drive(request: Request, file: UploadFile = File(...), parseConfig: str | None = Form(None)):
    """上传到个人文件库（不解析，status='uploaded'）。"""
    principal = get_principal(request)
    data = file.file.read()
    content_hash = hashlib.sha256(data).hexdigest()
    pcfg = _parse_cfg(parseConfig)
    method = pcfg.get("method") or "naive"
    # 租户级去重（已存→直接返回）
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM kb_file WHERE tenant_id=%s AND content_hash=%s",
                (principal.tenant_id, content_hash),
            )
            existed = cur.fetchone()
    if existed:
        return {"fileId": str(existed[0]), "status": existed[1], "reused": True}
    size_bytes = len(data)
    ok, _reason, _qinfo = check_quota(principal.tenant_id, size_bytes)
    if not ok:
        audit("FILE_UPLOAD", request, result="quota_exceeded")
        raise HTTPException(status_code=413, detail="KB_QUOTA_EXCEEDED")
    file_id = str(uuid.uuid4())
    storage_key = f"{file_id}/v1/raw"
    get_minio().put_object(settings.minio_bucket, storage_key, io.BytesIO(data), len(data))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,size_bytes,
                   status,owner_user_id,parser_type,parser_config)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'uploaded',%s,%s,%s)""",
                (file_id, principal.tenant_id, storage_key, file.filename, content_hash,
                 file.content_type, size_bytes, principal.user_id, method, Json(pcfg) if pcfg else None),
            )
            meter_ingest(conn, principal.tenant_id, size_bytes)
    audit("FILE_UPLOAD", request, result="ok")
    return {"fileId": file_id, "status": "uploaded"}


@router.get("/files")
def list_files(request: Request):
    principal = get_principal(request)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT f.id, f.name, f.status, f.size_bytes, f.parser_type, f.created_at,
                          (SELECT count(*) FROM kb_file_kb fk WHERE fk.file_id = f.id) AS kb_count
                   FROM kb_file f
                   WHERE f.tenant_id = %s AND f.owner_user_id = %s
                   ORDER BY f.created_at DESC""",
                (principal.tenant_id, principal.user_id),
            )
            rows = cur.fetchall()
    return [
        {
            "fileId": str(r["id"]),
            "name": r["name"],
            "status": r["status"],
            "sizeBytes": r["size_bytes"],
            "parserType": r["parser_type"],
            "kbCount": r["kb_count"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


@router.delete("/files/{file_id}")
def delete_file(file_id: str, request: Request):
    principal = get_principal(request)
    if not _can_manage_file(principal, file_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_FILE")
    deleted, err = purge_file(principal.tenant_id, file_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=err or "KB_FILE_NOT_FOUND")
    audit("FILE_DELETE", request, result="ok", detail={"file_id": file_id})
    return {"ok": True}


@router.patch("/files/{file_id}")
def rename_file(file_id: str, body: dict, request: Request):
    """重命名 / 更新 parseConfig（下次 attach/reparse 生效）。"""
    principal = get_principal(request)
    if not _can_manage_file(principal, file_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_FILE")
    sets, args = [], []
    if body.get("name"):
        sets.append("name=%s")
        args.append(body["name"])
    pcfg = _parse_cfg(body.get("parseConfig") or body.get("parserConfig"))
    if pcfg:
        sets.append("parser_config=%s")
        args.append(Json(pcfg))
        sets.append("parser_type=%s")
        args.append(pcfg.get("method") or "naive")
    if not sets:
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    args.append(file_id)
    args.append(principal.tenant_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE kb_file SET {', '.join(sets)} WHERE id=%s AND tenant_id=%s",
                args,
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="KB_FILE_NOT_FOUND")
    return {"ok": True}


@router.post("/files/{file_id}/attach")
def attach_file(file_id: str, body: dict, request: Request):
    """挂到 KB。未解析(status=uploaded)→按 parseConfig 解析→ready；已解析→仅链接（chunks 跨库共享）。"""
    principal = get_principal(request)
    decision = resolve_authz(principal)
    kb_id = body.get("kbId")
    if not kb_id or kb_id not in decision.allowed_kb_ids or not can_write(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    if not _can_manage_file(principal, file_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_FILE")
    pcfg = _parse_cfg(body.get("parseConfig") or body.get("parserConfig"))
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if pcfg:
                cur.execute(
                    "UPDATE kb_file SET parser_config=%s, parser_type=%s WHERE id=%s AND tenant_id=%s",
                    (Json(pcfg), pcfg.get("method") or "naive", file_id, principal.tenant_id),
                )
            cur.execute(
                "INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (file_id, kb_id, principal.tenant_id),
            )
            cur.execute("SELECT status FROM kb_file WHERE id=%s", (file_id,))
            row = cur.fetchone()
            status = row["status"] if row else None
    stats = None
    if status == "uploaded":  # 未解析 → 触发 ingest
        stats = pipeline.ingest_file(file_id)
        status = "ready"
    audit("FILE_ATTACH", request, kb_ids=[kb_id], result="ok", detail={"file_id": file_id})
    return {"fileId": file_id, "kbId": kb_id, "status": status, "stats": stats}


@router.post("/files/{file_id}/detach")
def detach_file(file_id: str, body: dict, request: Request):
    """从某 KB 解挂（文件保留在个人库 / 其它库；已建索引不撤）。"""
    principal = get_principal(request)
    decision = resolve_authz(principal)
    kb_id = body.get("kbId")
    if not kb_id or kb_id not in decision.allowed_kb_ids or not can_write(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    if not _can_manage_file(principal, file_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_FILE")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kb_file_kb WHERE file_id=%s AND kb_id=%s AND tenant_id=%s",
                (file_id, kb_id, principal.tenant_id),
            )
    audit("FILE_DETACH", request, kb_ids=[kb_id], result="ok", detail={"file_id": file_id})
    return {"ok": True}

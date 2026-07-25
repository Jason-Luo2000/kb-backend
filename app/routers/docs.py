"""文档：上传+摄取 / 状态 / 精读锚点。
T9：上传需 editor+ 且 kb ∈ allowed；read_anchor 与 /search 同级 ACL（红队越权修复）。"""
import hashlib
import io
import json
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.authz import can_write, is_kb_admin, is_tenant_owner, resolve as resolve_authz
from app.config import settings
from app.db import get_conn
from app.quota import check_quota, meter_ingest
from app.ingest import pipeline
from app.middleware.auth import audit, get_principal, limiter, verify_api_key
from app.metrics import INGEST_DURATION
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


def _kb_default_parser_config(kb_id: str) -> dict:
    """取该 KB 的 parser_config（新建文档未显式指定方法时的默认）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT parser_config FROM kb_kb WHERE id=%s", (kb_id,))
            row = cur.fetchone()
    if row and row[0]:
        return dict(row[0])
    return {}


@router.post("/kbs/{kb_id}/docs")
@limiter.limit("30/minute")
def upload_doc(kb_id: str, request: Request, file: UploadFile = File(...), parseConfig: str | None = Form(None)):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    if kb_id not in decision.allowed_kb_ids or not can_write(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    data = file.file.read()
    content_hash = hashlib.sha256(data).hexdigest()
    # C：分块配置（JSON 字符串）。未显式指定→落 KB 默认（每文档存自己的方法，与 RAGFlow 一致）
    pcfg: dict = {}
    if parseConfig:
        try:
            pcfg = json.loads(parseConfig) if isinstance(parseConfig, str) else dict(parseConfig)
        except Exception:  # noqa: BLE001
            pcfg = {}
    if not pcfg or not pcfg.get("method"):
        pcfg = {**_kb_default_parser_config(kb_id), **pcfg}
    method = pcfg.get("method") or "naive"
    # 幂等（#23）：同租户同 content_hash 命中 → 链 kb + 返回已存 file_id，不重摄
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM kb_file WHERE tenant_id=%s AND content_hash=%s",
                (principal.tenant_id, content_hash),
            )
            existed = cur.fetchone()
            if existed:
                cur.execute(
                    "INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (str(existed[0]), kb_id, principal.tenant_id),
                )
    if existed:
        audit("UPLOAD", request, kb_ids=[kb_id], result="reused", ua=request.headers.get("user-agent"))
        return {"docId": str(existed[0]), "status": existed[1], "reused": True}
    # T15 配额预检（新摄、去重确认后、MinIO put 前；reused 不计）
    size_bytes = len(data)
    ok, reason, qinfo = check_quota(principal.tenant_id, size_bytes)
    if not ok:
        audit("UPLOAD", request, kb_ids=[kb_id], result="quota_exceeded", detail=qinfo, ua=request.headers.get("user-agent"))
        raise HTTPException(status_code=413, detail="KB_QUOTA_EXCEEDED")
    file_id = str(uuid.uuid4())
    storage_key = f"{file_id}/v1/raw"
    get_minio().put_object(settings.minio_bucket, storage_key, io.BytesIO(data), len(data))
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kb_file(id,tenant_id,storage_key,name,content_hash,mime,size_bytes,status,owner_user_id,
                       parser_type,parser_config)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'parsing',%s,%s,%s)""",
                    (
                        file_id,
                        principal.tenant_id,
                        storage_key,
                        file.filename,
                        content_hash,
                        file.content_type,
                        size_bytes,
                        principal.user_id,
                        method,
                        Json(pcfg) if pcfg else None,
                    ),
                )
                cur.execute(
                    "INSERT INTO kb_file_kb(file_id,kb_id,tenant_id) VALUES (%s,%s,%s)",
                    (file_id, kb_id, principal.tenant_id),
                )
                meter_ingest(conn, principal.tenant_id, size_bytes)  # T15：用量计量（同事务原子）
    except Exception as e:  # noqa: BLE001
        audit("UPLOAD", request, kb_ids=[kb_id], result="fail")
        raise HTTPException(status_code=500, detail=f"ingest failed: {e}") from e
    _t0 = time.perf_counter()  # T16：upload→indexed SLO（仅 ingest_file 时长）
    try:
        stat = pipeline.ingest_file(file_id)
    except Exception as e:  # noqa: BLE001
        INGEST_DURATION.labels(principal.tenant_id, "fail").observe(time.perf_counter() - _t0)
        audit("UPLOAD", request, kb_ids=[kb_id], result="fail")
        raise HTTPException(status_code=500, detail=f"ingest failed: {e}") from e
    INGEST_DURATION.labels(principal.tenant_id, "ok").observe(time.perf_counter() - _t0)
    audit("UPLOAD", request, kb_ids=[kb_id], result="ok", ua=request.headers.get("user-agent"))
    return {"docId": file_id, "status": "ready", "stats": stat}


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


@router.get("/me")  # 前端用：验 key + 显身份 + 门控 owner/admin 页
def me(request: Request):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    is_owner = is_tenant_owner(principal)
    is_admin = is_owner or any(is_kb_admin(decision, kid) for kid in decision.allowed_kb_ids)
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "is_owner": is_owner,
        "is_admin": is_admin,
    }


@router.get("/kbs/{kb_id}/docs")  # 前端用：列库内文档
def list_kb_docs(kb_id: str, request: Request):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    if kb_id not in decision.allowed_kb_ids:
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT f.id, f.name, f.status, f.page_count, f.size_bytes, f.parser_type, f.parser_config
                   FROM kb_file_kb fk JOIN kb_file f ON f.id = fk.file_id
                   WHERE fk.kb_id = %s AND f.tenant_id = %s
                   ORDER BY f.created_at DESC""",
                (kb_id, principal.tenant_id),
            )
            rows = cur.fetchall()
    return [
        {
            "docId": str(r["id"]),
            "title": r["name"],
            "status": r["status"],
            "pages": r["page_count"],
            "sizeBytes": r["size_bytes"],
            "parserType": r["parser_type"],
            "parserConfig": r["parser_config"],
        }
        for r in rows
    ]


@router.get("/parser/methods")  # 前端用：列分块方法目录
def parser_methods(request: Request):
    from app.ingest import chunker_factory

    return chunker_factory.METHOD_INFO


# ============ F：库内文档管理（detach / reparse / rename / bulk）============
def _require_kb_write(kb_id: str, request: Request):
    principal = get_principal(request)
    decision = resolve_authz(principal)
    if kb_id not in decision.allowed_kb_ids or not can_write(decision, kb_id):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_KB")
    return principal


@router.delete("/kbs/{kb_id}/docs/{doc_id}")
def remove_doc_from_kb(kb_id: str, doc_id: str, request: Request):
    """把文档移出该 KB（解挂 kb_file_kb；文件保留在个人库/其它库，已建索引不撤）。"""
    _require_kb_write(kb_id, request)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kb_file_kb WHERE file_id=%s AND kb_id=%s",
                (doc_id, kb_id),
            )
            removed = cur.rowcount
    audit("DOC_REMOVE", request, kb_ids=[kb_id], result="ok" if removed else "noop", detail={"doc_id": doc_id})
    return {"ok": True, "removed": removed}


@router.post("/kbs/{kb_id}/docs/{doc_id}/reparse")
def reparse_doc(kb_id: str, doc_id: str, body: dict, request: Request):
    """重新解析（新版本；T11/T12 增量自动适用）。可选 parseConfig 覆盖。"""
    principal = _require_kb_write(kb_id, request)
    pcfg = body.get("parseConfig") if isinstance(body.get("parseConfig"), dict) else None
    _t0 = time.perf_counter()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if pcfg:
                    cur.execute(
                        "UPDATE kb_file SET parser_config=%s, parser_type=%s WHERE id=%s AND tenant_id=%s",
                        (Json(pcfg), pcfg.get("method") or "naive", doc_id, principal.tenant_id),
                    )
        stat = pipeline.ingest_file(doc_id)
    except Exception as e:  # noqa: BLE001
        INGEST_DURATION.labels(principal.tenant_id, "fail").observe(time.perf_counter() - _t0)
        raise HTTPException(status_code=500, detail=f"reparse failed: {e}") from e
    INGEST_DURATION.labels(principal.tenant_id, "ok").observe(time.perf_counter() - _t0)
    audit("DOC_REPARSE", request, kb_ids=[kb_id], result="ok", detail={"doc_id": doc_id})
    return {"docId": doc_id, "stats": stat}


@router.patch("/kbs/{kb_id}/docs/{doc_id}")
def update_doc(kb_id: str, doc_id: str, body: dict, request: Request):
    """重命名文档（改 kb_file.name）。"""
    principal = _require_kb_write(kb_id, request)
    name = (body.get("title") or body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="KB_VALIDATION")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE kb_file SET name=%s WHERE id=%s AND tenant_id=%s
                   AND EXISTS (SELECT 1 FROM kb_file_kb fk WHERE fk.file_id=kb_file.id AND fk.kb_id=%s)""",
                (name, doc_id, principal.tenant_id, kb_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="KB_DOC_NOT_FOUND")
    return {"ok": True}


@router.post("/kbs/{kb_id}/docs/bulk")
def bulk_docs(kb_id: str, body: dict, request: Request):
    """批量：{ids:[docId], action: delete|reparse}。delete=移出该 KB；reparse=逐个重摄。"""
    _require_kb_write(kb_id, request)
    ids = body.get("ids") or []
    action = body.get("action")
    done = []
    for doc_id in ids:
        if action == "delete":
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM kb_file_kb WHERE file_id=%s AND kb_id=%s",
                        (doc_id, kb_id),
                    )
            done.append(doc_id)
        elif action == "reparse":
            try:
                pipeline.ingest_file(doc_id)
                done.append(doc_id)
            except Exception:  # noqa: BLE001
                pass  # 单个失败不阻塞其余
    audit("DOC_BULK", request, kb_ids=[kb_id], result="ok", detail={"action": action, "count": len(done)})
    return {"action": action, "done": done}

"""运维端点（T14）：版本级 GC/purge + ES↔PG 对账 + outbox 修剪。

红队：GC/对账是租户级破坏性运维动作，**禁止作为 LLM 工具**（防 prompt-injection 触发数据清除），
仅 owner UI/SDK 显式调用。所有端点 owner-only、dry_run 默认开、apply 显式触发、审计内联。
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.authz import is_tenant_owner
from app.indexing.gc import prune_outbox, purge_versions
from app.indexing.reconcile import reconcile
from app.middleware.auth import get_principal, verify_api_key

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(verify_api_key)])


def _require_owner(request: Request):
    p = get_principal(request)
    if not is_tenant_owner(p):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_OWNER")
    return p


@router.post("/gc")
def gc(body: dict, request: Request):
    """旧版本 GC。{fileId?, dryRun=true}。dry_run 默认开；apply 需 dryRun=false。"""
    p = _require_owner(request)
    return purge_versions(
        p.tenant_id,
        file_id=body.get("fileId"),
        dry_run=body.get("dryRun", True),
        principal_user_id=p.user_id,
    )


@router.post("/reconcile")
def reconcile_ep(body: dict, request: Request):
    """ES↔PG 对账。{fileId?, dryRun=true, repair=true}。dry_run 默认开。"""
    p = _require_owner(request)
    return reconcile(
        p.tenant_id,
        file_id=body.get("fileId"),
        dry_run=body.get("dryRun", True),
        repair=body.get("repair", True),
        principal_user_id=p.user_id,
    )


@router.post("/outbox/prune")
def prune_ep(body: dict, request: Request):
    """修剪已发布 outbox 行。{retainDays?}。"""
    p = _require_owner(request)
    return prune_outbox(retain_days=body.get("retainDays"), principal_user_id=p.user_id)

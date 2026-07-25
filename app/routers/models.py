"""模型 provider 管理端点（M，owner-only）。

红队：含 API-key，**禁止作为 LLM 工具**（防 prompt-injection 套取 key / 改默认模型）。
仅 owner UI/SDK 显式调用。系统内置行（tenant_id NULL，env 种子）只读：列表可见、不可改删
（update/delete 带 tenant_id 时天然不匹配 NULL 行）。审计内联。
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.adapters import embedder, llm, rerank
from app.authz import is_tenant_owner
from app.middleware.auth import audit, get_principal, verify_api_key
from app.models_registry import (
    create_model,
    defaults_view,
    delete_model,
    get_model_config,
    list_models,
    update_model,
)

router = APIRouter(prefix="/v1/admin/models", dependencies=[Depends(verify_api_key)])


def _require_owner(request: Request):
    p = get_principal(request)
    if not is_tenant_owner(p):
        raise HTTPException(status_code=403, detail="KB_FORBIDDEN_OWNER")
    return p


@router.get("")
def list_ep(kind: str | None = None, request: Request = None):
    p = _require_owner(request)
    rows = list_models(p.tenant_id, include_system=True)
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    return rows


@router.get("/defaults")
def defaults_ep(request: Request):
    p = _require_owner(request)
    return defaults_view(p.tenant_id)


@router.post("")
def create_ep(body: dict, request: Request):
    p = _require_owner(request)
    if not body.get("modelName"):
        raise HTTPException(status_code=400, detail="modelName required")
    try:
        out = create_model(p.tenant_id, body)
    except ValueError as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e
    audit("MODEL_CREATE", request, result="ok", detail={"kind": body.get("kind")})
    return out


@router.patch("/{model_id}")
def update_ep(model_id: str, body: dict, request: Request):
    p = _require_owner(request)
    ok = update_model(model_id, p.tenant_id, body)
    if not ok:
        raise HTTPException(status_code=404, detail="KB_MODEL_NOT_FOUND")
    audit("MODEL_UPDATE", request, result="ok", detail={"model_id": model_id})
    return {"ok": True}


@router.delete("/{model_id}")
def delete_ep(model_id: str, request: Request):
    p = _require_owner(request)
    ok = delete_model(model_id, p.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="KB_MODEL_NOT_FOUND")
    audit("MODEL_DELETE", request, result="ok", detail={"model_id": model_id})
    return {"ok": True}


@router.post("/{model_id}/test")
def test_ep(model_id: str, request: Request):
    """测连通：按 id 解析该行配置 → 一次 chat/embed/rerank。失败返 {ok:False, detail}。"""
    p = _require_owner(request)
    cfg = get_model_config(model_id, p.tenant_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="KB_MODEL_NOT_FOUND")
    runner = {"llm": llm.test_model, "embedding": embedder.test_model, "rerank": rerank.test_model}.get(cfg.kind)
    if runner is None:
        raise HTTPException(status_code=400, detail=f"unknown kind {cfg.kind}")
    try:
        msg = runner(cfg)
    except Exception as e:  # noqa: BLE001
        audit("MODEL_TEST", request, result="fail", detail={"model_id": model_id, "err": str(e)[:120]})
        return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:200]}"}
    audit("MODEL_TEST", request, result="ok", detail={"model_id": model_id})
    return {"ok": True, "detail": msg}

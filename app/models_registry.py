"""模型 provider 注册表（M：多 provider 模型配置）。

解析顺序：tenant 默认（DB）→ 系统内置（tenant_id=NULL，env 种子）→ env 兜底（pre-bootstrap / 无配置）。
tenant_id 经 contextvar 自动透传（auth 中间件 set；同请求线程内的 summarizer/embedder/orchestrator 无需改签名）。
rerank 无 env 兜底 → 未配置时 resolve 返回 None（orchestrator 保持 RRF）。
"""
import contextvars
import uuid
from dataclasses import dataclass

from psycopg.rows import dict_row

from app import crypto
from app.config import settings
from app.db import get_conn

CURRENT_TENANT: contextvars.ContextVar[str | None] = contextvars.ContextVar("kb_current_tenant", default=None)

KINDS = ("llm", "embedding", "rerank")


@dataclass
class ModelConfig:
    id: str | None
    kind: str
    provider_type: str
    base_url: str
    api_key: str  # 解密后明文（仅内存）
    model_name: str
    dim: int | None
    name: str = ""
    is_default: bool = False


def set_tenant(tenant_id: str | None) -> None:
    CURRENT_TENANT.set(tenant_id)


def get_tenant() -> str | None:
    return CURRENT_TENANT.get()


def _row_to_cfg(row: dict) -> ModelConfig:
    return ModelConfig(
        id=str(row["id"]),
        kind=row["kind"],
        provider_type=row["provider_type"],
        base_url=row["base_url"] or "",
        api_key=crypto.decrypt(row["api_key_enc"]),
        model_name=row["model_name"],
        dim=row["dim"],
        name=row["name"],
        is_default=bool(row["is_default"]),
    )


def _get_default(kind: str, tenant_id: str | None) -> ModelConfig | None:
    try:
        with get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if tenant_id is None:
                    cur.execute(
                        """SELECT * FROM kb_model_config
                           WHERE tenant_id IS NULL AND kind=%s AND is_default=1 LIMIT 1""",
                        (kind,),
                    )
                else:
                    cur.execute(
                        """SELECT * FROM kb_model_config
                           WHERE tenant_id=%s AND kind=%s AND is_default=1 LIMIT 1""",
                        (tenant_id, kind),
                    )
                row = cur.fetchone()
    except Exception:  # noqa: BLE001  表未建/DB 未就绪 → 走 env 兜底
        return None
    return _row_to_cfg(row) if row else None


def _env_fallback(kind: str) -> ModelConfig | None:
    """pre-bootstrap / 无配置兜底：从 env 智谱配置合成（rerank 无 → None）。"""
    if not settings.zhipu_api_key:
        return None
    if kind == "llm":
        return ModelConfig(None, "llm", "anthropic", settings.zhipu_llm_base_url, settings.zhipu_api_key,
                           settings.zhipu_llm_model, None, "zhipu-llm(env)", True)
    if kind == "embedding":
        return ModelConfig(None, "embedding", "openai", settings.zhipu_embed_base_url, settings.zhipu_api_key,
                           settings.zhipu_embed_model, settings.zhipu_embed_dim, "zhipu-embed(env)", True)
    return None  # rerank 无 env 兜底 → 未配置


def resolve_model(kind: str, tenant_id: str | None = None) -> ModelConfig | None:
    """租户默认 → 系统内置 → env 兜底。rerank 未配置返回 None。"""
    tid = tenant_id or CURRENT_TENANT.get()
    if tid:
        cfg = _get_default(kind, tid)
        if cfg:
            return cfg
    cfg = _get_default(kind, None)
    if cfg:
        return cfg
    return _env_fallback(kind)


def get_model_config(model_id: str, tenant_id: str | None = None) -> ModelConfig | None:
    """按 id 取一条（含解密 key）。tenant_id 给定时仅返回本租户或系统行（owner 可测系统默认）。
    供 /test 端点用——不复用 resolve（要测指定行，非生效默认）。"""
    try:
        with get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if tenant_id is None:
                    cur.execute("SELECT * FROM kb_model_config WHERE id=%s", (model_id,))
                else:
                    cur.execute(
                        """SELECT * FROM kb_model_config
                           WHERE id=%s AND (tenant_id=%s OR tenant_id IS NULL)""",
                        (model_id, tenant_id),
                    )
                row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _row_to_cfg(row) if row else None


# ============ 管理（owner-only 端点用）============
def _mask(key: str) -> str:
    if not key:
        return ""
    return ("*" * max(0, len(key) - 4)) + key[-4:] if len(key) > 4 else "****"


def list_models(tenant_id: str | None, include_system: bool = True) -> list[dict]:
    rows: list[dict] = []
    try:
        with get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if include_system:
                    cur.execute(
                        """SELECT * FROM kb_model_config
                           WHERE (tenant_id=%s OR tenant_id IS NULL) ORDER BY kind, created_at""",
                        (tenant_id,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM kb_model_config WHERE tenant_id=%s ORDER BY kind, created_at",
                        (tenant_id,),
                    )
                rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        cfg = _row_to_cfg(r)
        out.append({
            "id": cfg.id,
            "name": cfg.name,
            "kind": cfg.kind,
            "providerType": cfg.provider_type,
            "baseUrl": cfg.base_url,
            "apiKey": _mask(crypto.decrypt(r["api_key_enc"])),
            "hasKey": bool(crypto.decrypt(r["api_key_enc"])),
            "modelName": cfg.model_name,
            "dim": cfg.dim,
            "isDefault": cfg.is_default,
            "system": r["tenant_id"] is None,
        })
    return out


def _clear_default(tenant_id: str | None, kind: str) -> None:
    """清掉该 tenant（或系统）下某 kind 的其它默认。系统行(tenant_id NULL)用 IS NULL。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if tenant_id is None:
                cur.execute(
                    "UPDATE kb_model_config SET is_default=0 WHERE tenant_id IS NULL AND kind=%s",
                    (kind,),
                )
            else:
                cur.execute(
                    "UPDATE kb_model_config SET is_default=0 WHERE tenant_id=%s AND kind=%s",
                    (tenant_id, kind),
                )


def create_model(tenant_id: str | None, body: dict) -> dict:
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError("kind must be one of " + ",".join(KINDS))
    mid = str(uuid.uuid4())
    is_default = bool(body.get("isDefault"))
    if is_default:
        _clear_default(tenant_id, kind)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_model_config(id,tenant_id,name,kind,provider_type,base_url,api_key_enc,
                   model_name,dim,is_default)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    mid, tenant_id, body.get("name") or body.get("modelName"),
                    kind, body.get("providerType", "openai"), body.get("baseUrl"),
                    crypto.encrypt(body.get("apiKey", "")),
                    body["modelName"], body.get("dim"), 1 if is_default else 0,
                ),
            )
    return {"id": mid}


def update_model(model_id: str, tenant_id: str | None, body: dict) -> bool:
    """仅可改本 tenant 行（系统行只 owner 显式管理；这里 tenant_id 非 None 时不碰系统行）。"""
    sets: list[str] = []
    args: list = []
    for f_in, f_db in (("name", "name"), ("providerType", "provider_type"), ("baseUrl", "base_url"),
                       ("modelName", "model_name"), ("dim", "dim")):
        if f_in in body:
            sets.append(f"{f_db}=%s")
            args.append(body[f_in])
    if "apiKey" in body and body["apiKey"]:
        sets.append("api_key_enc=%s")
        args.append(crypto.encrypt(body["apiKey"]))
    if "isDefault" in body:
        sets.append("is_default=%s")
        args.append(1 if body["isDefault"] else 0)
    if not sets:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            if tenant_id is None:
                cur.execute(f"SELECT kind, tenant_id FROM kb_model_config WHERE id=%s", (model_id,))
            else:
                cur.execute(
                    f"SELECT kind, tenant_id FROM kb_model_config WHERE id=%s AND tenant_id=%s",
                    (model_id, tenant_id),
                )
            row = cur.fetchone()
            if not row:
                return False
            kind, row_tid = row
            if body.get("isDefault"):
                _clear_default(row_tid, kind)
            args.append(model_id)
            cur.execute(f"UPDATE kb_model_config SET {', '.join(sets)} WHERE id=%s", args)
    return True


def delete_model(model_id: str, tenant_id: str | None) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if tenant_id is None:
                cur.execute("DELETE FROM kb_model_config WHERE id=%s", (model_id,))
            else:
                cur.execute(
                    "DELETE FROM kb_model_config WHERE id=%s AND tenant_id=%s",
                    (model_id, tenant_id),
                )
            return cur.rowcount > 0


def defaults_view(tenant_id: str | None) -> dict:
    """当前各 kind 生效的默认（解析顺序同 resolve_model）。"""
    out = {}
    for kind in KINDS:
        cfg = resolve_model(kind, tenant_id)
        out[kind] = None if cfg is None else {
            "id": cfg.id,
            "name": cfg.name,
            "providerType": cfg.provider_type,
            "baseUrl": cfg.base_url,
            "modelName": cfg.model_name,
            "dim": cfg.dim,
        }
    return out

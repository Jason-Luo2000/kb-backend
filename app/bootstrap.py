"""启动时初始化：PG 表 / ES 索引 / MinIO bucket / default 租户与身份。
容器 CMD 与本地均可调用。"""
import hashlib
import pathlib
import uuid

from app.config import settings
from app.db import get_conn
from app.es import ensure_index
from app.storage import ensure_bucket

# 确定性命名空间：使 default tenant/user/api_key 的 id 跨重启稳定
NAMESPACE = uuid.UUID("7b3a2c1e-5d4f-4a8b-9c6e-1f2d3a4b5c6d")


def _tenant_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"tenant:{name}"))


def _user_id(external_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"user:{external_id}"))


def default_tenant_id() -> str:
    return _tenant_id(settings.default_tenant_name)


def default_user_id() -> str:
    return _user_id(settings.kb_user_id)


def _run_schema() -> None:
    import re

    schema_sql = (pathlib.Path(__file__).parent / "schema.sql").read_text()
    schema_sql = re.sub(r"--[^\n]*", "", schema_sql)  # 去行内注释（含中文，避免 ascii 编码失败）
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)


def bootstrap_identities() -> None:
    """幂等种 default 租户 + owner 用户 + api_key。
    使现有 e2e/pi-ext 用 KB_API_KEY + KB_USER_ID 无需改动即落入 default 租户。"""
    tid = default_tenant_id()
    uid = default_user_id()
    key_hash = hashlib.sha256(settings.kb_api_key.encode()).hexdigest()
    apikey_id = str(uuid.uuid5(NAMESPACE, f"apikey:{key_hash[:32]}"))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_tenant(id,name) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING",
                (tid, settings.default_tenant_name),
            )
            cur.execute(
                "INSERT INTO kb_user(id,external_id) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING",
                (uid, settings.kb_user_id),
            )
            # 确保 default 用户是 default 租户的 owner
            cur.execute(
                """INSERT INTO kb_user_tenant(user_id,tenant_id,role) VALUES (%s,%s,'owner')
                   ON CONFLICT (user_id,tenant_id) DO UPDATE SET role='owner'""",
                (uid, tid),
            )
            cur.execute(
                """INSERT INTO kb_api_key(id,tenant_id,user_id,key_hash,scopes)
                   VALUES (%s,%s,%s,%s,'["*"]'::jsonb)
                   ON CONFLICT (key_hash) DO NOTHING""",
                (apikey_id, tid, uid, key_hash),
            )


def run() -> None:
    _run_schema()
    ensure_index()
    ensure_bucket()
    bootstrap_identities()
    print(
        f"bootstrap done: PG schema + ES index + MinIO bucket + default tenant/user/api_key ready "
        f"(tenant={default_tenant_id()} user={default_user_id()})"
    )


if __name__ == "__main__":
    run()

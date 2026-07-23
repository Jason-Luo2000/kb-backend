"""启动时初始化：PG 表 / ES 索引 / MinIO bucket。容器 CMD 与本地均可调用。"""
import pathlib

from app.db import get_conn
from app.es import ensure_index
from app.storage import ensure_bucket


def run() -> None:
    import re

    schema_sql = (pathlib.Path(__file__).parent / "schema.sql").read_text()
    schema_sql = re.sub(r"--[^\n]*", "", schema_sql)  # 去行内注释（含中文，避免 ascii 编码失败）
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
    ensure_index()
    ensure_bucket()
    print("bootstrap done: PG schema + ES index + MinIO bucket ready")


if __name__ == "__main__":
    run()

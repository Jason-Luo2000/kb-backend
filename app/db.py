"""PostgreSQL 访问（MVP 同步、每操作一连接；后期换连接池）。"""
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from app.config import PG_DSN


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(PG_DSN, autocommit=False)
    conn.execute("SET CLIENT_ENCODING TO 'UTF8'")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

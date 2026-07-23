"""知识库管理：GET/POST /v1/kbs。"""
import uuid

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.db import get_conn
from app.middleware.auth import verify_api_key

router = APIRouter(prefix="/v1/kbs", dependencies=[Depends(verify_api_key)])


@router.get("")
def list_kbs():
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT k.id, k.name, k.description, k.created_at,
                          (SELECT count(*) FROM kb_file_kb fk WHERE fk.kb_id = k.id) AS doc_count
                   FROM kb_kb k ORDER BY k.created_at"""
            )
            rows = cur.fetchall()
    return [
        {"id": str(r["id"]), "name": r["name"], "description": r["description"], "docCount": r["doc_count"]}
        for r in rows
    ]


@router.post("")
def create_kb(body: dict):
    kid = str(uuid.uuid4())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_kb(id,name,description) VALUES (%s,%s,%s)",
                (kid, body["name"], body.get("description")),
            )
    return {"id": kid, "name": body["name"]}

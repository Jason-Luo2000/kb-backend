"""跨租户红队测试（验收 A5）：双租户互不可见、read_anchor 不越权、cite 不跨租户、grant 可见性/can_write。

直接 PG 造 tenant/user/api_key，再以各自 token 走 HTTP API 断言零泄露。
前置：库已 bootstrap、服务在 KB_BACKEND_URL（默认 http://localhost:8001）。
运行：.venv/bin/python scripts/cross_tenant_test.py
"""
import hashlib
import os
import sys
import tempfile
import uuid

import httpx
import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.bootstrap import NAMESPACE  # noqa: E402
from app.config import PG_DSN  # noqa: E402

BASE = os.getenv("KB_BACKEND_URL", "http://localhost:8001")


def _tid(name):
    return str(uuid.uuid5(NAMESPACE, f"tenant:{name}"))


def _uid(ext):
    return str(uuid.uuid5(NAMESPACE, f"user:{ext}"))


def _kh(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Cli:
    def __init__(self, token: str):
        self.h = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {token}", "X-KB-Client": "redteam/1.0"})

    def req(self, method, path, **kw):
        return self.h.request(method, BASE + path, **kw)


def setup_tenants():
    """造 tenant A/B 各一 owner + api_key；A 额外一个 viewer 用户（无 grant）。返回 (creds, doc_texts)。"""
    run = uuid.uuid4().hex[:8]
    creds = {}
    docs = {}
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            for tag, tname, uext, token in [("A", "tenantA", "ownerA", "key_A_secret"),
                                            ("B", "tenantB", "ownerB", "key_B_secret")]:
                tid, uid = _tid(tname), _uid(uext)
                cur.execute("INSERT INTO kb_tenant(id,name) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name", (tid, tname))
                cur.execute("INSERT INTO kb_user(id,external_id) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET external_id=EXCLUDED.external_id", (uid, uext))
                cur.execute("INSERT INTO kb_user_tenant(user_id,tenant_id,role) VALUES(%s,%s,'owner') ON CONFLICT(user_id,tenant_id) DO UPDATE SET role='owner'", (uid, tid))
                cur.execute("INSERT INTO kb_api_key(id,tenant_id,user_id,key_hash) VALUES(%s,%s,%s,%s) ON CONFLICT(key_hash) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,user_id=EXCLUDED.user_id", (str(uuid.uuid4()), tid, uid, _kh(token)))
                secret = f"{tag}_SECRET_{run}"
                creds[tag] = {"token": token, "tenant_id": tid, "user_id": uid}
                docs[tag] = f"# {tag} 独占文档 {run}\n\n本段含唯一密语 {secret}，仅本租户可见。跨租户检索绝不应命中此处内容。\n"
            # A 的 viewer 用户（无任何 grant）
            u2, tok2 = _uid("viewerA"), "key_A_viewer_secret"
            cur.execute("INSERT INTO kb_user(id,external_id) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING", (u2, "viewerA"))
            cur.execute("INSERT INTO kb_user_tenant(user_id,tenant_id,role) VALUES(%s,%s,'viewer') ON CONFLICT(user_id,tenant_id) DO UPDATE SET role='viewer'", (u2, creds["A"]["tenant_id"]))
            cur.execute("INSERT INTO kb_api_key(id,tenant_id,user_id,key_hash) VALUES(%s,%s,%s,%s) ON CONFLICT(key_hash) DO NOTHING", (str(uuid.uuid4()), creds["A"]["tenant_id"], u2, _kh(tok2)))
            creds["A_viewer"] = {"token": tok2, "user_id": u2}
        conn.commit()
    return creds, docs, run


def upload_doc(cli: Cli, doc_text: str) -> tuple[str, str]:
    kb = cli.req("POST", "/v1/kbs", json={"name": f"kb-{uuid.uuid4().hex[:6]}"}).json()
    kb_id = kb["id"]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(doc_text)
        path = f.name
    try:
        with open(path, "rb") as fh:
            r = cli.req("POST", f"/v1/kbs/{kb_id}/docs", files={"file": (f"{kb_id}.md", fh, "text/markdown")})
        r.raise_for_status()
        doc_id = r.json()["docId"]
    finally:
        os.unlink(path)
    # 取一个 chunkId（search 本租户密语）
    secret = [w for w in doc_text.split() if "_SECRET_" in w][0]
    res = cli.req("POST", "/v1/search", json={"query": secret}).json()
    chunk_id = res["hits"][0]["chunkId"] if res["hits"] else ""
    return kb_id, doc_id, chunk_id


def main():
    creds, docs, run = setup_tenants()
    A = Cli(creds["A"]["token"])
    B = Cli(creds["B"]["token"])
    Av = Cli(creds["A_viewer"]["token"])
    a_secret = f"A_SECRET_{run}"
    b_secret = f"B_SECRET_{run}"

    kbA, docA, chunkA = upload_doc(A, docs["A"])
    kbB, docB, chunkB = upload_doc(B, docs["B"])
    print(f"setup: A kb={kbA[:8]} doc={docA[:8]} chunk={chunkA[:8]} | B kb={kbB[:8]} doc={docB[:8]} chunk={chunkB[:8]}")

    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    # 1. A 检索只命中 A，不含 B 密语
    res = A.req("POST", "/v1/search", json={"query": "SECRET"}).json()
    blob = " ".join(h["snippet"] for h in res["hits"])
    check("A 检索无 B 密语", b_secret not in blob, f"hits={len(res['hits'])}")
    check("A 检索含 A 密语", a_secret in blob)

    # 2. A 列库只见 A 的 kb，不见 B
    ids = {k["id"] for k in A.req("GET", "/v1/kbs").json()}
    check("A 列库无 B 的 kb", kbB not in ids, f"A sees {len(ids)} kb")

    # 3. read_anchor 越权：A 读 B 的 doc → 403
    r = A.req("POST", "/v1/read-anchor", json={"docId": docB, "anchor": chunkB})
    check("A read_anchor(B) → 403", r.status_code == 403, f"got {r.status_code}")

    # 4. cite 越权：A 回传 B 的 chunkId → 该 chunk 被滤掉（references 无 B）
    r = A.req("POST", "/v1/cite", json={"answer": "x", "chunkIds": [chunkB]}).json()
    leaked = [ref for ref in r.get("references", []) if ref.get("chunkId") == chunkB]
    check("A cite(B chunk) 无泄露", not leaked, f"refs={len(r.get('references', []))}")

    # 5. A 上传到 B 的 kb → 403
    r = A.req("POST", f"/v1/kbs/{kbB}/docs", files={"file": ("x.md", b"# x", "text/markdown")})
    check("A upload 到 B kb → 403", r.status_code == 403, f"got {r.status_code}")

    # 6. 租户内 grant：A viewer 无 grant 时不见 kbA
    vids = {k["id"] for k in Av.req("GET", "/v1/kbs").json()}
    check("A viewer 无 grant 不见 kbA", kbA not in vids, f"viewer sees {len(vids)}")
    # owner grant viewer
    gr = A.req("PUT", "/v1/acl", json={"kbId": kbA, "userId": creds["A_viewer"]["user_id"], "role": "viewer"})
    check("owner grant viewer → 200", gr.status_code == 200, gr.text[:80])
    vids = {k["id"] for k in Av.req("GET", "/v1/kbs").json()}
    check("A viewer grant 后见 kbA", kbA in vids)
    # viewer 不可写
    r = Av.req("POST", f"/v1/kbs/{kbA}/docs", files={"file": ("x.md", b"# y SECRET", "text/markdown")})
    check("A viewer upload → 403", r.status_code == 403, f"got {r.status_code}")

    # 7. viewer grant 无效 token（伪造）→ 401
    rogue = Cli("not-a-real-key")
    check("伪造 token → 401", rogue.req("GET", "/v1/kbs").status_code == 401)

    print(f"\n{'ALL GREEN ✅' if not fails else 'FAILURES ❌ ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

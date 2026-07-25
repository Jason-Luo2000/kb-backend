"""kb-sdk 1.0 重试/错误单测（T17）。用 httpx.MockTransport（内置，无新依赖），无需服务。
钉死 review #15：仅幂等重试、5xx/429/网络触发、4xx 立即抛、错误分类映射。
运行：.venv/bin/pytest tests/test_sdk_retry.py -q"""
import os
import sys

# kb_sdk 在 sdk/ 下，加入路径（同 e2e_demo）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

import httpx
import pytest

from kb_sdk import KBClient, KBError, KBForbidden, KBRateLimited, KBServerError, KBValidation


def _client(handler, **kw):
    return KBClient("http://x", "k", max_retries=2, backoff_base=0, transport=httpx.MockTransport(handler), **kw)


def test_idempotent_retries_5xx_then_ok():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json={"ok": True})

    c = _client(h)
    assert c.health() == {"ok": True}
    assert calls["n"] == 2


def test_idempotent_exhausts_then_server_error():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(500, json={})

    c = _client(h)
    with pytest.raises(KBServerError):
        c.search("q")
    assert calls["n"] == 3  # max_retries(2) + 1


def test_429_retried_then_rate_limited():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(429, json={})

    c = _client(h)
    with pytest.raises(KBRateLimited):
        c.search("q")
    assert calls["n"] == 3  # 429 可重试


def test_non_idempotent_no_retry():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(500, json={})

    c = _client(h)
    with pytest.raises(KBServerError):
        c.create_kb("n")  # 非幂等：不重试
    assert calls["n"] == 1


def test_4xx_no_retry_mapped():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(403, json={"detail": "KB_FORBIDDEN_KB"})

    c = _client(h)
    with pytest.raises(KBForbidden) as e:
        c.search("q")
    assert e.value.code == "KB_FORBIDDEN_KB"
    assert calls["n"] == 1  # 4xx 不重试


def test_422_validation_list_detail():
    def h(req):
        return httpx.Response(422, json={"detail": [{"msg": "field required"}]})

    c = _client(h)
    with pytest.raises(KBValidation):
        c.search("q")


def test_network_error_idempotent_retries():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    c = _client(h)
    with pytest.raises(KBError) as e:
        c.search("q")
    assert e.value.code == "KB_NETWORK"
    assert calls["n"] == 3  # 网络/超时也重试（幂等时）


def test_network_error_non_idempotent_no_retry():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    c = _client(h)
    with pytest.raises(KBError):
        c.create_kb("n")
    assert calls["n"] == 1


def test_gc_dry_run_is_idempotent_apply_is_not():
    # dry_run=True 重试；dry_run=False 不重试
    dry_calls = {"n": 0}
    apply_calls = {"n": 0}

    def h_dry(req):
        dry_calls["n"] += 1
        return httpx.Response(500, json={})

    def h_apply(req):
        apply_calls["n"] += 1
        return httpx.Response(500, json={})

    with pytest.raises(KBServerError):
        _client(h_dry).gc(dry_run=True)
    with pytest.raises(KBServerError):
        _client(h_apply).gc(dry_run=False)
    assert dry_calls["n"] == 3 and apply_calls["n"] == 1


def test_upload_sends_idempotency_key(tmp_path):
    seen = {}
    fp = tmp_path / "f.md"
    fp.write_bytes(b"# hi\nbody")

    def h(req):
        seen["key"] = req.headers.get("idempotency-key")
        seen["ct"] = req.headers.get("content-type", "")
        return httpx.Response(200, json={"docId": "d", "status": "ready"})

    r = _client(h).upload("kbid", str(fp))
    assert r["docId"] == "d"
    assert seen["key"]  # Idempotency-Key 已发（前向兼容）
    assert seen["ct"].startswith("multipart/form-data")

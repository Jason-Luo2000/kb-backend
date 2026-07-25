"""kb-sdk 结构化错误（T17）。把后端 HTTP 状态 + FastAPI detail 映射到 KB_* 分类。

后端 HTTPException 返回 {"detail": "KB_FORBIDDEN_KB"}；422 校验错误 detail 为列表。
review #15：客户端拿到结构化错误而非裸 raise_for_status。
"""


class KBError(Exception):
    code = "KB_ERROR"

    def __init__(self, message: str, *, status: int = 0, request_id: str | None = None, code: str | None = None):
        self.message = message
        self.status = status
        self.request_id = request_id
        if code:
            self.code = code
        super().__init__(f"{self.code} ({status}): {message}")


class KBUnauthorized(KBError):
    code = "KB_UNAUTHORIZED"


class KBForbidden(KBError):
    code = "KB_FORBIDDEN_KB"


class KBNotFound(KBError):
    code = "KB_NOT_FOUND"


class KBValidation(KBError):
    code = "KB_VALIDATION"


class KBAnchorStale(KBError):
    code = "KB_ANCHOR_STALE"


class KBQuotaExceeded(KBError):
    code = "KB_QUOTA_EXCEEDED"


class KBRateLimited(KBError):
    code = "KB_RATE_LIMITED"


class KBServerError(KBError):
    code = "KB_SERVER_ERROR"


_STATUS_DEFAULTS = {
    401: KBUnauthorized,
    403: KBForbidden,
    404: KBNotFound,
    422: KBValidation,
    429: KBRateLimited,
}
_CODE_CLASS = {c.code: c for c in (
    KBUnauthorized, KBForbidden, KBNotFound, KBValidation,
    KBAnchorStale, KBQuotaExceeded, KBRateLimited, KBServerError,
)}


def from_response(resp) -> KBError:
    """按 detail（KB_* 串）→ 状态默认 → 兜底，构造对应 KBError 子类。"""
    status = getattr(resp, "status_code", 0) or 0
    detail = ""
    request_id = None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = None
    if isinstance(body, dict):
        d = body.get("detail")
        request_id = body.get("requestId") or body.get("request_id")
        if isinstance(d, str):
            detail = d
        elif isinstance(d, list):  # FastAPI 422 校验：[{msg,...}]
            detail = "; ".join(str(x.get("msg", x)) for x in d if isinstance(x, dict)) or "validation error"
    if detail.startswith("KB_") and detail in _CODE_CLASS:
        cls = _CODE_CLASS[detail]
        code = detail
    elif status in _STATUS_DEFAULTS:
        cls = _STATUS_DEFAULTS[status]
        code = cls.code
    elif status >= 500:
        cls = KBServerError
        code = KBServerError.code
    else:
        cls = KBError
        code = "KB_ERROR"
    return cls(detail or f"HTTP {status}", status=status, request_id=request_id, code=code)

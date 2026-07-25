"""kb-sdk 1.0：kb-backend 客户端（幂等重试 + 结构化错误，T17）。"""
from .client import KBClient
from .errors import (
    KBAnchorStale,
    KBError,
    KBForbidden,
    KBNotFound,
    KBQuotaExceeded,
    KBRateLimited,
    KBServerError,
    KBUnauthorized,
    KBValidation,
)

__version__ = "1.0.0"

__all__ = [
    "KBClient",
    "KBError",
    "KBUnauthorized",
    "KBForbidden",
    "KBNotFound",
    "KBValidation",
    "KBAnchorStale",
    "KBQuotaExceeded",
    "KBRateLimited",
    "KBServerError",
]

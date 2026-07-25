"""模型 API-key 对称加密（best-effort，非公开网络用；外部 KMS/WORM 见后期）。

Fernet（cryptography）：key 由 settings.model_secret 经 sha256 派生为合法 32B Fernet key——
口令可任意字符串（dev 默认），生产用 MODEL_SECRET 覆盖。加解密失败 best-effort 返回空串
（key 换过→旧密文解不出→前端提示重输），不抛、不阻塞。
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings

_instance: Fernet | None = None


def _key() -> bytes:
    """任意口令 → 合法 Fernet key（urlsafe base64 of sha256(口令)）。"""
    return base64.urlsafe_b64encode(hashlib.sha256(settings.model_secret.encode()).digest())


def _fernet() -> Fernet:
    global _instance
    if _instance is None:
        _instance = Fernet(_key())
    return _instance


def encrypt(plain: str | None) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:  # noqa: BLE001  key 换过/损坏 → 空串（前端提示重输）
        return ""

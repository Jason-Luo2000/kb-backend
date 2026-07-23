"""MinIO 对象存储（原文 / 解析中间产物 / 总结文档）。"""
from minio import Minio

from app.config import settings

_client: Minio | None = None


def get_minio():
    global _client
    if _client is None:
        if settings.store_mode == "memory":
            from app.storage_memory import FakeMinio

            _client = FakeMinio()
        else:
            _client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=False,
            )
    return _client


def ensure_bucket() -> None:
    mc = get_minio()
    if not mc.bucket_exists(settings.minio_bucket):
        mc.make_bucket(settings.minio_bucket)

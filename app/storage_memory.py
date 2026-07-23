"""内存/本地文件版 MinIO 模拟（无容器环境验证用；生产用真 MinIO）。"""
import os
import pathlib


class _Resp:
    def __init__(self, data: bytes):
        self._d = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n == -1:
            d = self._d[self._pos :]
            self._pos = len(self._d)
        else:
            d = self._d[self._pos : self._pos + n]
            self._pos += n
        return d

    def close(self):
        pass

    def release_conn(self):
        pass


class FakeMinio:
    def __init__(self):
        self._root = pathlib.Path(os.getenv("KB_DATA_DIR", str(pathlib.Path.cwd() / ".data")))

    def _path(self, bucket, key):
        return self._root / bucket / key

    def bucket_exists(self, bucket):
        return (self._root / bucket).exists()

    def make_bucket(self, bucket):
        (self._root / bucket).mkdir(parents=True, exist_ok=True)

    def put_object(self, bucket, key, data, length, **kw):
        p = self._path(bucket, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.read() if hasattr(data, "read") else bytes(data))

    def get_object(self, bucket, key):
        return _Resp(self._path(bucket, key).read_bytes())

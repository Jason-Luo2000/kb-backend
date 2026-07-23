"""64-bit simhash（路 A 稳定锚重定位用，评审 #4/#20）。

字符 bigram 特征 → 各 64-bit hash → 按位加权累加 → 符号位指纹。
Hamming ≤ SAME_SEG 视为同段（重定位判据）。sha256 只能校验相等，simhash 能模糊比对——
文档重切分后旧 chunk 文本的 simhash 仍能在新 chunk 集合里就近命中。

注：方案提 LSH-forest 避免全表 Hamming 扫描；T10 在「同 file 内」扫描（每文件数十块），
规模足够小，正确性不变。LSH-forest 留作后期规模优化。
"""
import hashlib

SAME_SEG = 3  # Hamming ≤3 视为同段
_MASK = (1 << 64) - 1
_SIGN = 1 << 63


def to_signed(v: int) -> int:
    """64bit 无符号 → PG/ES BIGINT(signed)（bit63 置位则 -2^64）。"""
    v &= _MASK
    return v - (1 << 64) if v & _SIGN else v


def to_unsigned(v: int) -> int:
    """PG/ES BIGINT(signed) → 64bit 无符号（Hamming 比较前转回）。"""
    return v + (1 << 64) if v < 0 else v


def features(text: str) -> list[str]:
    """字符 bigram 特征（simhash 与锚点选择 query 重叠共用）。"""
    s = "".join(str(text).lower().split())  # 归一化空白/大小写
    if len(s) < 2:
        return [s] if s else []
    return [s[i : i + 2] for i in range(len(s) - 1)]


def _hash64(feat: str) -> int:
    return int.from_bytes(hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    v = [0] * 64
    feats = features(text)
    for feat in feats:
        h = _hash64(feat)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    fp = 0
    for i in range(64):
        if v[i] > 0:
            fp |= 1 << i
    return fp


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def same_segment(a: int, b: int, threshold: int = SAME_SEG) -> bool:
    return hamming(a, b) <= threshold


def simhash_hex(text: str) -> str:
    """16-hex 归一化（64bit），适配 CHAR(16) 列（fingerprint / content_fingerprint）。"""
    return format(simhash(text), "016x")


def hamming_hex(a_hex: str, b_int: int) -> int:
    """anchor 存 hex(无符号)、kb_chunk 存 BIGINT(signed) 时的跨表示 Hamming。"""
    return hamming(int(a_hex, 16), to_unsigned(b_int))


"""Ed25519 서명 (RFC 8032) - 표준 라이브러리만으로.

업데이트 폴더에 올린 새 버전이 관리자가 올린 것인지 확인하는 데 쓴다. 관리자 PC 만 비밀 키(seed)를
갖고, 각 PC 는 공개 키로 latest.json 의 서명을 확인한다 (latest.json 안의 sha256 이 exe 를 묶는다).
"""
from __future__ import annotations

import hashlib
import os

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


# 점은 확장 좌표 (X, Y, Z, T): x = X/Z, y = Y/Z, x*y = T/Z

def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _double(p):
    a = p[0] * p[0] % _P
    b = p[1] * p[1] % _P
    c = 2 * p[2] * p[2] % _P
    h = a + b
    e = h - (p[0] + p[1]) ** 2
    g = a - b
    f = c + g
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


_ZERO = (0, 1, 1, 0)


def _mul(s: int, p):
    q = _ZERO
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _double(p)
        s >>= 1
    return q


def _equal(p, q) -> bool:
    return ((p[0] * q[2] - q[0] * p[2]) % _P == 0
            and (p[1] * q[2] - q[1] * p[2]) % _P == 0)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)

def _base_table() -> tuple:
    out, p = [], _G
    for _ in range(256):
        out.append(p)
        p = _double(p)
    return tuple(out)


# 2^i * G (i = 0..255) - 기준점 곱셈을 덧셈만으로. 불러올 때 한 번 만든다 (약 1ms):
# 처음 쓸 때 만들면 일꾼 스레드 둘이 동시에 서명을 확인할 때 덜 만든 표를 볼 수 있다.
_BASE_TABLE = _base_table()


def _base_mul(s: int):
    q = _ZERO
    i = 0
    while s > 0:
        if s & 1:
            q = _add(q, _BASE_TABLE[i])
        s >>= 1
        i += 1
    return q


def _compress(p) -> bytes:
    zinv = _inv(p[2])
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _hash_int(*parts: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ValueError("Ed25519 비밀 키는 32바이트입니다")
    h = hashlib.sha512(bytes(seed)).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


# ----------------------------------------------------------------- 공개 ---

def generate_seed() -> bytes:
    return os.urandom(32)


def public_key(seed: bytes) -> bytes:
    a, _prefix = _expand(seed)
    return _compress(_base_mul(a))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    pub = _compress(_base_mul(a))
    r = _hash_int(prefix, msg) % _L
    big_r = _compress(_base_mul(r))
    h = _hash_int(big_r, pub, msg) % _L
    s = (r + h * a) % _L
    return big_r + s.to_bytes(32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    """서명이 맞으면 True. 잘못된 입력(길이, 점이 아닌 값, S >= L 등)은 모두 False."""
    try:
        pub, msg, sig = bytes(pub), bytes(msg), bytes(sig)
        if len(pub) != 32 or len(sig) != 64:
            return False
        a_point = _decompress(pub)
        r_point = _decompress(sig[:32])
        if a_point is None or r_point is None:
            return False
        s = int.from_bytes(sig[32:], "little")
        if s >= _L:
            return False
        h = _hash_int(sig[:32], pub, msg) % _L
        return _equal(_base_mul(s), _add(r_point, _mul(h, a_point)))
    except Exception:
        return False


def fingerprint(pub_hex: str) -> str:
    """사람이 눈으로 맞춰 볼 짧은 지문. 예: 3F2A-91C0-7B4E. 잘못된 값이면 ""."""
    try:
        pub = bytes.fromhex(str(pub_hex).strip())
    except ValueError:
        return ""
    if len(pub) != 32:
        return ""
    digest = hashlib.sha256(pub).hexdigest()[:12].upper()
    return "-".join(digest[i:i + 4] for i in range(0, 12, 4))

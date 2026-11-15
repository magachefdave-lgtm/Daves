"""VNC 비밀번호 보관 - Windows DPAPI로 암호화해 이 PC, 이 사용자 계정에서만 풀린다.

설비 목록 파일(공유될 수 있음)에는 비밀번호를 넣지 않고, 프로그램 폴더의
passwords.json 에 따로 둔다.
"""
from __future__ import annotations

import base64
import json
import os
import sys

AVAILABLE = sys.platform == "win32"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = BLOB()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("DPAPI 처리 실패")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


class PasswordStore:
    def __init__(self, path: str, encrypt=None, decrypt=None):
        self.path = path
        self._enc = encrypt or (lambda b: _dpapi(b, True))
        self._dec = decrypt or (lambda b: _dpapi(b, False))

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def get(self, eq_id: str) -> str:
        blob = self._load().get(eq_id)
        if not blob:
            return ""
        try:
            return self._dec(base64.b64decode(blob)).decode("utf-8")
        except (OSError, ValueError):
            return ""  # 다른 PC/계정에서 저장한 값 - 풀 수 없으면 없는 것으로 본다

    def has(self, eq_id: str) -> bool:
        return bool(self._load().get(eq_id))

    def set(self, eq_id: str, password: str) -> None:
        data = self._load()
        if password:
            data[eq_id] = base64.b64encode(self._enc(password.encode("utf-8"))).decode("ascii")
        elif eq_id in data:
            del data[eq_id]
        else:
            return
        self._save(data)

    def set_many(self, passwords: dict) -> None:
        data = self._load()
        for eq_id, password in passwords.items():
            data[eq_id] = base64.b64encode(self._enc(password.encode("utf-8"))).decode("ascii")
        self._save(data)

    def delete(self, ids) -> None:
        data = self._load()
        kept = {k: v for k, v in data.items() if k not in set(ids)}
        if len(kept) != len(data):
            self._save(kept)

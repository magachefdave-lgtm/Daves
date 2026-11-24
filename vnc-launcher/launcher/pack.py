"""배포본: 지금 PC의 설정·설비 목록·VNC 비밀번호를 exe 끝에 붙여 넣은 실행 파일.

받은 사람이 그 exe 를 처음 실행하면 붙어 있는 내용을 자기 설정 폴더로 가져온다.
PyInstaller exe 는 끝에서부터 자기 내용을 찾으므로, 그 뒤에 덧붙인 데이터는 실행을 막지 않는다
(Authenticode 서명이 exe 끝에 붙는 것과 같은 자리).

모양: [원래 exe][MAGIC][내용 JSON(UTF-8)][JSON 길이 8바이트][TRAILER]

비밀번호는 Windows 계정마다 따로 암호화돼 있어(DPAPI) 그대로 옮길 수 없으므로, 배포본 안에서는
배포 암호로 다시 암호화한다. 배포 암호가 없으면 프로그램에 들어 있는 고정 키를 쓰는데, 이것은
가리기일 뿐이다 - exe 를 가진 사람은 누구나 꺼낼 수 있다.
"""
from __future__ import annotations

import base64
import datetime
import getpass
import hashlib
import hmac
import json
import os
import shutil
import socket
import sys
import uuid

MAGIC = b"\n#ERPACK1#\n"
TRAILER = b"#ERPACK-END#"
_BUILTIN = "EquipmentRemote/no-passphrase/2026"   # 배포 암호를 정하지 않았을 때 (가리기)
_ITER = 200_000


class PackError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 문장을 담는다."""


def own_exe() -> str | None:
    """지금 실행 중인 exe. 개발 중(파이썬으로 실행)에는 없다 - 시험할 때는 환경 변수로 준다."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.environ.get("EQUIPMENT_REMOTE_EXE") or None


def _find(path: str) -> tuple[int, dict] | None:
    """(내용이 시작하는 위치, 내용). 붙은 것이 없으면 None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            tail = len(TRAILER) + 8
            if size < tail + len(MAGIC):
                return None
            f.seek(size - tail)
            raw = f.read(tail)
            if not raw.endswith(TRAILER):
                return None
            length = int.from_bytes(raw[:8], "big")
            start = size - tail - length - len(MAGIC)
            if length <= 0 or start < 0:
                return None
            f.seek(start)
            if f.read(len(MAGIC)) != MAGIC:
                return None
            data = json.loads(f.read(length).decode("utf-8"))
    except (OSError, ValueError):
        return None
    return (start, data) if isinstance(data, dict) and data.get("format") == 1 else None


def read_pack(path: str | None) -> dict | None:
    found = _find(path) if path else None
    return found[1] if found else None


def write_pack(src_exe: str, dst: str, payload: dict) -> None:
    """src_exe 를 복사하고 끝에 payload 를 붙인다. src 에 이미 붙어 있던 것은 떼고 붙인다."""
    if os.path.abspath(src_exe) == os.path.abspath(dst):
        raise PackError("지금 실행 중인 파일에는 덮어쓸 수 없습니다. 다른 이름으로 저장하세요.")
    body = json.dumps({**payload, "format": 1}, ensure_ascii=False).encode("utf-8")
    tmp = dst + ".tmp"
    try:
        with open(src_exe, "rb") as src, open(tmp, "wb") as out:
            _copy_body(src_exe, src, out)
            out.write(MAGIC + body + len(body).to_bytes(8, "big") + TRAILER)
        os.replace(tmp, dst)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise PackError(f"배포본을 저장하지 못했습니다.\n{dst}\n({e})") from e


def _copy_body(path: str, src, out) -> None:
    """exe 의 본래 부분만 복사한다 (붙어 있는 배포본 내용은 뺀다)."""
    found = _find(path)
    if found is None:
        shutil.copyfileobj(src, out)
        return
    remaining = found[0]
    while remaining:
        chunk = src.read(min(1 << 20, remaining))
        if not chunk:
            break
        out.write(chunk)
        remaining -= len(chunk)


def strip_pack(src_exe: str, dst: str) -> None:
    """배포본에서 붙은 내용을 뗀 순수한 exe 를 dst 에 쓴다 (배포본이 아니면 그대로 복사)."""
    with open(src_exe, "rb") as src, open(dst, "wb") as out:
        _copy_body(src_exe, src, out)


# ------------------------------------------------------------ 비밀번호 봉인 ---

def _keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    k = hashlib.pbkdf2_hmac("sha256", (passphrase or _BUILTIN).encode("utf-8"), salt, _ITER, dklen=64)
    return k[:32], k[32:]


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def seal(data: dict, passphrase: str) -> dict:
    """data(JSON 으로 바꿀 수 있는 것)를 배포 암호로 암호화한다 (HMAC-SHA256 키스트림 + HMAC 확인값)."""
    salt, nonce = os.urandom(16), os.urandom(16)
    ek, mk = _keys(passphrase, salt)
    plain = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(plain, _stream(ek, nonce, len(plain))))
    tag = hmac.new(mk, salt + nonce + ct, hashlib.sha256).hexdigest()
    return {"salt": salt.hex(), "nonce": nonce.hex(), "data": base64.b64encode(ct).decode("ascii"),
            "tag": tag, "passphrase": bool(passphrase)}


def unseal(sealed: dict, passphrase: str):
    """배포 암호가 틀리거나 내용이 손상됐으면 ValueError."""
    try:
        salt, nonce = bytes.fromhex(sealed["salt"]), bytes.fromhex(sealed["nonce"])
        ct = base64.b64decode(sealed["data"])
        tag = sealed["tag"]
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("손상된 배포본") from e
    ek, mk = _keys(passphrase, salt)
    if not hmac.compare_digest(hmac.new(mk, salt + nonce + ct, hashlib.sha256).hexdigest(), tag):
        raise ValueError("배포 암호가 맞지 않습니다")
    plain = bytes(a ^ b for a, b in zip(ct, _stream(ek, nonce, len(ct))))
    return json.loads(plain.decode("utf-8"))


def make_payload(config: dict, equipment: dict | None, passwords: dict[str, str] | None,
                 passphrase: str, update_key: str = "") -> dict:
    """배포본에 넣을 내용. equipment 는 {"groups": [...], "items": [...]} 또는 None(공유 목록을 쓸 때).
    update_key: 받는 PC가 믿을 업데이트 서명 키 (공개 키 16진수, 없으면 "")."""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return {
        "id": uuid.uuid4().hex,
        "made": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "by": f"{os.environ.get('COMPUTERNAME') or socket.gethostname()} / {user}",
        "config": config,
        "equipment": equipment,
        "passwords": seal(passwords, passphrase) if passwords else None,
        "password_count": len(passwords or {}),
        "update_key": update_key,
    }

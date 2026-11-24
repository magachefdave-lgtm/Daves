"""서버(공유 폴더)를 통한 업데이트.

업데이트 폴더에는 두 파일만 둔다:
  EquipmentRemote.exe   새 버전
  latest.json           {"version", "sha256", "size", "notes", "published", "by"}

관리자는 새 버전을 실행한 뒤 [더보기 > 업데이트 올리기]로 두 파일을 올리고(publish),
각 PC 는 켤 때 latest.json 을 읽어 지금 버전보다 새것이면 알린다. [업데이트]를 누르면 install:
  1. 새 exe 를 지금 exe 옆에 .new 로 복사하고 sha256 으로 확인
  2. 실행 중인 exe 를 .old 로 이름만 바꾼다 (Windows 는 실행 중인 exe 를 덮어쓰지 못하지만 이름은 바꿀 수 있다)
  3. .new 를 원래 이름으로, 새 exe 를 켜고 지금 것은 끈다. .old 는 다음에 켤 때 지운다.
exe 가 쓰기 금지된 폴더에 있으면 사용자 설정 폴더에 새 exe 를 받는다.

서명: 관리자 PC 의 비밀 키(SigningKeyStore)로 latest.json 에 Ed25519 서명을 붙인다. latest.json 안의
sha256 이 exe 를 묶으므로, 서명을 확인한 정보로 install 하면 exe 도 관리자가 올린 것임이 확인된다.
각 PC 는 처음 본 서명 키를 기억해 두고(verify_info 의 "trust_new"), 그 뒤로는 같은 키의 서명만 받는다.
"""
from __future__ import annotations

import base64
import datetime
import getpass
import hashlib
import json
import os
import re
import socket
import subprocess
import sys

from . import sign
from .pack import seal, strip_pack, unseal

LATEST = "latest.json"
EXE = "EquipmentRemote.exe"


class UpdateError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 문장을 담는다."""


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for p in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_latest(folder: str) -> dict | None:
    """업데이트 폴더의 latest.json. 아직 올린 것이 없으면 None, 읽을 수 없으면 UpdateError."""
    path = os.path.join(folder, LATEST)
    if not os.path.exists(path):
        if not os.path.isdir(folder):
            raise UpdateError(f"업데이트 폴더에 접근할 수 없습니다.\n{folder}\n\n"
                              "네트워크 연결과 폴더 권한, [더보기 > 설정]의 업데이트 폴더 경로를 확인하세요.")
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            info = json.load(f)
        if not isinstance(info, dict) or not info.get("version") or not info.get("sha256"):
            raise ValueError("version/sha256 없음")
    except (OSError, ValueError) as e:
        raise UpdateError(f"업데이트 정보를 읽을 수 없습니다.\n{path}\n({e})") from e
    return info


def signed_bytes(info: dict) -> bytes:
    """서명하는 내용: signature 를 뺀 latest.json 전체를 정해진 모양의 JSON 으로."""
    body = {k: v for k, v in info.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def verify_info(info: dict, trusted_key: str | None) -> tuple[str, str]:
    """latest.json 의 서명을 확인한다. (판정, 정보 안의 공개 키 hex)

    "ok"         서명이 맞고, 이 PC 가 믿는 키로 서명했다
    "trust_new"  서명이 맞고, 이 PC 는 아직 믿는 키가 없다 (부르는 쪽이 이 키를 기억한다)
    "unsigned"   서명이 없고, 이 PC 도 믿는 키가 없다 (예전 방식 - 받는다)
    "bad"        그 밖의 모든 경우 (서명이 틀림, 다른 키, 믿는 키가 있는데 서명이 없음, 모양이 틀림)
    """
    trusted = str(trusted_key or "").strip().lower()
    if not isinstance(info, dict):
        return "bad", ""
    key, sig = info.get("key"), info.get("signature")
    key_hex = key.strip().lower() if isinstance(key, str) else ""
    if key is None and sig is None:
        return ("bad" if trusted else "unsigned"), ""
    if not isinstance(key, str) or not isinstance(sig, str):
        return "bad", key_hex
    if not re.fullmatch(r"[0-9a-f]{64}", key_hex) or not re.fullmatch(r"[0-9a-fA-F]{128}", sig.strip()):
        return "bad", key_hex   # bytes.fromhex 는 빈칸 섞인 값도 받으므로 모양을 먼저 본다
    pub, signature = bytes.fromhex(key_hex), bytes.fromhex(sig.strip())
    if not sign.verify(pub, signed_bytes(info), signature):
        return "bad", key_hex
    if not trusted:
        return "trust_new", key_hex
    return ("ok" if key_hex == trusted else "bad"), key_hex


def publish(src_exe: str, folder: str, version: str, notes: str,
            signing_seed: bytes | None = None) -> dict:
    """새 버전을 업데이트 폴더에 올린다. exe 를 먼저, latest.json 을 나중에 바꾼다
    (그 사이에 받는 PC 는 예전 정보를 보거나, 확인값이 안 맞아 "다시 시도"가 된다 - 망가진 exe 를 받지 않는다).
    signing_seed(관리자의 서명 키)를 주면 latest.json 에 공개 키(key)와 서명(signature)을 붙인다."""
    if signing_seed is not None and (not isinstance(signing_seed, (bytes, bytearray)) or len(signing_seed) != 32):
        raise UpdateError("업데이트 서명 키가 올바르지 않아 올리지 않았습니다.")
    dst = os.path.join(folder, EXE)
    tmp = dst + ".uploading"
    try:
        os.makedirs(folder, exist_ok=True)
        strip_pack(src_exe, tmp)            # 배포본이면 붙은 설정은 떼고 올린다
        digest, size = sha256_of(tmp), os.path.getsize(tmp)
        os.replace(tmp, dst)
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
        info = {"version": version, "file": EXE, "sha256": digest, "size": size, "notes": notes.strip(),
                "published": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "by": f"{os.environ.get('COMPUTERNAME') or socket.gethostname()} / {user}"}
        if signing_seed is not None:
            info["key"] = sign.public_key(signing_seed).hex()
            info["signature"] = sign.sign(signing_seed, signed_bytes(info)).hex()
        jtmp = os.path.join(folder, LATEST + ".tmp")
        with open(jtmp, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        os.replace(jtmp, os.path.join(folder, LATEST))
        return info
    except OSError as e:
        for p in (tmp,):
            try:
                os.remove(p)
            except OSError:
                pass
        raise UpdateError(f"업데이트 폴더에 올리지 못했습니다.\n{folder}\n\n"
                          f"폴더 쓰기 권한과, 그 폴더의 exe 를 누가 실행 중이지 않은지 확인하세요.\n({e})") from e


def _writable(folder: str) -> bool:
    probe = os.path.join(folder, f".write_test_{os.getpid()}")
    try:
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def is_network_path(path: str) -> bool:
    r"""\\서버\공유 또는 네트워크 드라이브(Z: 등)에 있는지."""
    if path.startswith(("\\\\", "//")):
        return True
    path = os.path.abspath(path)
    if os.name == "nt" and len(path) >= 2 and path[1] == ":":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(path[:2] + "\\") == 4   # DRIVE_REMOTE
        except Exception:
            return False
    return False


def install(folder: str, info: dict, current_exe: str, fallback_dir: str) -> str:
    """새 exe 를 받아 지금 exe 자리에 놓는다. 새 exe 의 경로를 돌려준다.
    지금 exe 의 폴더에 쓸 수 없거나 그 폴더가 네트워크에 있으면 fallback_dir 에 받는다
    (공유 폴더의 exe 를 여러 PC 가 실행 중일 때 한 PC 가 그 파일을 바꿔 치우지 않게)."""
    src = os.path.join(folder, os.path.basename(info.get("file") or EXE))
    here = os.path.dirname(os.path.abspath(current_exe))
    in_place = not is_network_path(here) and _writable(here)
    target = os.path.abspath(current_exe) if in_place else os.path.join(fallback_dir, EXE)
    new, old = target + ".new", target + ".old"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(src, "rb") as s, open(new, "wb") as d:
            for chunk in iter(lambda: s.read(1 << 20), b""):
                d.write(chunk)
    except OSError as e:
        _remove(new)
        raise UpdateError(f"새 버전을 받지 못했습니다.\n{src}\n({e})") from e
    if sha256_of(new) != info.get("sha256"):
        _remove(new)
        raise UpdateError("받은 파일이 올린 파일과 다릅니다 (받는 중 끊겼거나, 마침 새로 올리는 중).\n"
                          "잠시 뒤 다시 시도하세요.")
    try:
        _remove(old)
        if os.path.exists(target):
            os.replace(target, old)          # 실행 중이어도 이름은 바꿀 수 있다
        try:
            os.replace(new, target)
        except OSError:
            if os.path.exists(old) and not os.path.exists(target):
                os.replace(old, target)      # 되돌린다
            raise
    except OSError as e:
        _remove(new)
        raise UpdateError(f"새 버전으로 바꾸지 못했습니다.\n{target}\n({e})") from e
    return target


def cleanup_old(current_exe: str | None) -> None:
    """지난 업데이트에서 남은 .old 를 지운다 (그 exe 는 이제 실행 중이 아니다)."""
    if current_exe:
        _remove(os.path.abspath(current_exe) + ".old")


def relaunch(exe: str, args: tuple[str, ...] = ()) -> None:
    """새 exe 를 켠다. 이 프로그램(PyInstaller 한 파일 exe)이 같은 종류의 exe 를 켤 때는
    PYINSTALLER_RESET_ENVIRONMENT 를 줘야 새 exe 가 지금 것의 임시 폴더를 빌려 쓰다 죽지 않는다."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([exe, *args], env=env, cwd=os.path.dirname(exe), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ------------------------------------------------------------ 서명 키 ---

KEY_FILE = "update_signing_key.json"
_KEY_KIND = "EquipmentRemote update signing key"


def _dpapi_protect(data: bytes) -> bytes:
    from .secret import _dpapi
    return _dpapi(data, True)


def _dpapi_unprotect(data: bytes) -> bytes:
    from .secret import _dpapi
    return _dpapi(data, False)


class SigningKeyStore:
    """관리자 PC 의 업데이트 서명 키 (설정 폴더의 update_signing_key.json).

    비밀 키는 Windows DPAPI 로 암호화해 이 PC·이 계정에서만 풀린다. 다른 PC 로 옮기거나 잃어버릴 때를
    대비해 배포 암호처럼 암호를 걸어 내보내고(export) 가져올(import_file) 수 있다.
    """

    def __init__(self, path: str, encrypt=None, decrypt=None):
        self.path = path
        windows = sys.platform == "win32"
        self._protected = encrypt is not None or windows
        self._enc = encrypt or (_dpapi_protect if windows else (lambda b: b))
        self._dec = decrypt or (_dpapi_unprotect if windows else (lambda b: b))

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def _read(self) -> dict:
        with open(self.path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("public"), str) or not data.get("seed"):
            raise ValueError("서명 키 파일 모양이 아님")
        return data

    def public_hex(self) -> str:
        try:
            pub = self._read()["public"].strip().lower()
            return pub if len(bytes.fromhex(pub)) == 32 else ""
        except (OSError, ValueError, TypeError):
            return ""

    def load(self) -> bytes:
        if not self.exists():
            raise UpdateError("이 PC에는 업데이트 서명 키가 없습니다.")
        try:
            data = self._read()
        except (OSError, ValueError) as e:
            raise UpdateError(f"업데이트 서명 키 파일을 읽을 수 없습니다.\n{self.path}\n({e})") from e
        try:
            seed = self._dec(base64.b64decode(data["seed"]))
        except (OSError, ValueError, TypeError) as e:
            raise UpdateError(
                "업데이트 서명 키를 풀 수 없습니다.\n"
                "서명 키는 키를 만든 PC의 그 Windows 계정에서만 풀립니다. 다른 PC나 계정이라면 "
                "키 백업 파일을 가져오세요.") from e
        try:
            ok = len(seed) == 32 and sign.public_key(seed).hex() == data["public"].strip().lower()
        except ValueError:
            ok = False
        if not ok:
            raise UpdateError(f"업데이트 서명 키 파일이 손상되었습니다.\n{self.path}\n\n"
                              "키 백업 파일이 있으면 그것을 가져오세요.")
        return seed

    def _save(self, seed: bytes) -> None:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            data = {"format": 1, "public": sign.public_key(seed).hex(),
                    "seed": base64.b64encode(self._enc(seed)).decode("ascii"),
                    "protected": self._protected,
                    "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            _remove(tmp)
            raise UpdateError(f"업데이트 서명 키를 저장하지 못했습니다.\n{self.path}\n({e})") from e

    def create(self) -> bytes:
        """새 서명 키를 만든다. 이미 있으면 바꾸지 않는다 (바꾸면 다른 PC들이 업데이트를 거부한다)."""
        if self.exists():
            raise UpdateError("이 PC에는 이미 업데이트 서명 키가 있습니다. 새로 만들지 않았습니다.")
        seed = sign.generate_seed()
        self._save(seed)
        return seed

    def export(self, dst: str, passphrase: str) -> None:
        """서명 키를 암호를 걸어 파일로 내보낸다 (다른 관리자 PC 로 옮기거나 잃어버릴 때 대비)."""
        if not passphrase:
            raise UpdateError("키 백업 암호를 입력하세요.")
        seed = self.load()
        data = {"format": 1, "kind": _KEY_KIND, "public": sign.public_key(seed).hex(),
                "sealed": seal(seed.hex(), passphrase)}
        tmp = dst + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, dst)
        except OSError as e:
            _remove(tmp)
            raise UpdateError(f"키 백업 파일을 저장하지 못했습니다.\n{dst}\n({e})") from e

    def import_file(self, src: str, passphrase: str, replace: bool = False) -> str:
        """키 백업 파일을 가져온다. 공개 키 hex 를 돌려준다.
        암호가 틀리면 ValueError. 이 PC 에 다른 키가 이미 있으면 replace=True 일 때만 바꾼다."""
        try:
            with open(src, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if (not isinstance(data, dict) or data.get("kind") != _KEY_KIND
                    or not isinstance(data.get("sealed"), dict) or not isinstance(data.get("public"), str)):
                raise ValueError("서명 키 백업 파일이 아님")
        except (OSError, ValueError) as e:
            raise UpdateError(f"서명 키 백업 파일을 읽을 수 없습니다.\n{src}\n({e})") from e
        try:
            seed_hex = unseal(data["sealed"], passphrase)
        except ValueError as e:
            raise ValueError("키 백업 암호가 맞지 않습니다.") from e
        try:
            seed = bytes.fromhex(str(seed_hex))
            pub = sign.public_key(seed).hex()
        except ValueError as e:
            raise UpdateError(f"서명 키 백업 파일이 손상되었습니다.\n{src}") from e
        if pub != data["public"].strip().lower():
            raise UpdateError(f"서명 키 백업 파일이 손상되었습니다.\n{src}")
        current = self.public_hex()
        if self.exists() and current != pub and not replace:
            raise UpdateError("이 PC에는 이미 다른 업데이트 서명 키가 있습니다.\n"
                              f"지금 키: {sign.fingerprint(current) or '읽을 수 없음'}\n"
                              f"가져올 키: {sign.fingerprint(pub)}")
        self._save(seed)
        return pub


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass

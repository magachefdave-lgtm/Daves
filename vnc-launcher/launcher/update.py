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
"""
from __future__ import annotations

import datetime
import getpass
import hashlib
import json
import os
import socket
import subprocess

from .pack import strip_pack

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


def publish(src_exe: str, folder: str, version: str, notes: str) -> dict:
    """새 버전을 업데이트 폴더에 올린다. exe 를 먼저, latest.json 을 나중에 바꾼다
    (그 사이에 받는 PC 는 예전 정보를 보거나, 확인값이 안 맞아 "다시 시도"가 된다 - 망가진 exe 를 받지 않는다)."""
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


def install(folder: str, info: dict, current_exe: str, fallback_dir: str) -> str:
    """새 exe 를 받아 지금 exe 자리에 놓는다. 새 exe 의 경로를 돌려준다.
    지금 exe 의 폴더에 쓸 수 없으면 fallback_dir 에 받는다."""
    src = os.path.join(folder, os.path.basename(info.get("file") or EXE))
    here = os.path.dirname(os.path.abspath(current_exe))
    in_place = _writable(here)
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


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass

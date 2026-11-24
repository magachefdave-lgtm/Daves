"""소스 zip 만들기: 저장소와 같은 배치(vnc-launcher/, .github/workflows/)로 묶는다.

  python tools/make_source_zip.py EquipmentRemote_v1.4.7_source.zip

zip 을 저장소 맨 위 폴더에 풀면 그대로 제자리에 들어간다.
한글 파일 이름은 두 가지로 적는다.
  - 기본 이름: CP949 (한글 Windows 탐색기가 깨지지 않고 읽는다)
  - 0x7075 Unicode Path 추가 필드: UTF-8 (리눅스·맥의 unzip, 7-Zip 이 이것을 읽는다)
"""
from __future__ import annotations

import os
import struct
import sys
import time
import zipfile
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # vnc-launcher
SKIP_DIRS = {"__pycache__", ".pytest_cache", "build", "dist", ".git", "_github_workflows", "backup"}
SKIP_FILES = {"config.ini", "실행오류_안내.txt"}
# 소스 폴더 옆에 config.ini 가 있으면 그 폴더가 설정 폴더가 된다 - 거기 쌓인 개인 파일은 넣지 않는다
SKIP_PREFIX = ("equipment.json", "passwords.json", "ui_state.json", "update_signing_key", "접속이력_",
               "업데이트서명키_백업")
SKIP_EXT = (".pyc", ".spec", ".zip", ".exe", ".tmp", ".lock", ".bak")


class _Cp949Info(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        try:
            return self.filename.encode("ascii"), self.flag_bits
        except UnicodeEncodeError:
            return self.filename.encode("cp949"), self.flag_bits & ~0x800


def _unicode_path_extra(name: str) -> bytes:
    raw = name.encode("cp949")
    utf = name.encode("utf-8")
    body = struct.pack("<BI", 1, zlib.crc32(raw) & 0xFFFFFFFF) + utf
    return struct.pack("<HH", 0x7075, len(body)) + body


def _entries() -> list[tuple[str, str]]:
    out = []
    for folder, dirs, files in os.walk(HERE):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f in SKIP_FILES or f.endswith(SKIP_EXT) or f.startswith(SKIP_PREFIX):
                continue
            full = os.path.join(folder, f)
            out.append((full, "vnc-launcher/" + os.path.relpath(full, HERE).replace(os.sep, "/")))
    workflows = os.path.join(os.path.dirname(HERE), ".github", "workflows")
    if not os.path.isdir(workflows):           # 저장소 밖에서 풀어 둔 소스 (예전 zip 배치)
        workflows = os.path.join(HERE, "_github_workflows")
    if os.path.isdir(workflows):
        for f in sorted(os.listdir(workflows)):
            if f.endswith(".yml"):
                out.append((os.path.join(workflows, f), ".github/workflows/" + f))
    return out


def make(dst: str) -> int:
    entries = _entries()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for full, name in entries:
            info = _Cp949Info(name, time.localtime(os.path.getmtime(full))[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            if not name.isascii():
                info.extra = _unicode_path_extra(name)
            with open(full, "rb") as f:
                z.writestr(info, f.read())
    return len(entries)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(f"{make(sys.argv[1])} files -> {sys.argv[1]}")

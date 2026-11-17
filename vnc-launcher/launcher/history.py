"""접속 이력 - 언제, 어느 PC에서, 누가, 어느 설비에 접속했는지.

설비 목록 파일과 같은 폴더에 달마다 CSV 하나(접속이력_2026-11.csv)를 쌓는다. 목록을 공유
폴더에 두면 이력도 모든 PC 것이 한곳에 모인다. 한 줄씩 열고-쓰고-닫으므로 여러 PC가 같이 써도
섞이지 않고, 쓰기에 실패해도(엑셀로 열어 둔 경우 등) 접속은 그대로 진행한다.
"""
from __future__ import annotations

import csv
import datetime
import getpass
import os
import shutil
import socket
import tempfile

COLUMNS = ("시각", "PC", "사용자", "설비명", "IP", "동작", "비고")


def _who() -> tuple[str, str]:
    pc = os.environ.get("COMPUTERNAME") or socket.gethostname()
    try:
        user = os.environ.get("USERNAME") or getpass.getuser()
    except Exception:   # 계정 이름을 알 수 없는 환경
        user = ""
    return pc, user


class ConnectionLog:
    def __init__(self, folder: str, now=datetime.datetime.now):
        self.folder = folder
        self.now = now
        self.pc, self.user = _who()

    def path(self, when: datetime.datetime | None = None) -> str:
        when = when or self.now()
        return os.path.join(self.folder, f"접속이력_{when:%Y-%m}.csv")

    def write(self, action: str, name: str, host: str, note: str = "") -> bool:
        when = self.now()
        try:
            os.makedirs(self.folder, exist_ok=True)
            # utf-8-sig: 빈 파일에 쓸 때만 BOM 을 붙인다 (엑셀이 한글을 바로 읽는다)
            with open(self.path(when), "a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if f.tell() == 0:
                    w.writerow(COLUMNS)
                w.writerow([f"{when:%Y-%m-%d %H:%M:%S}", self.pc, self.user, name, host, action, note])
            return True
        except OSError:
            return False

    def view_copy(self) -> str | None:
        """이번 달 이력을 임시 폴더에 복사한 경로. 원본을 엑셀로 바로 열면 엑셀이 파일을 잠가
        그동안 다른 PC의 이력이 남지 않으므로, 볼 때는 복사본을 연다. 이력이 없으면 None."""
        src = self.path()
        if not os.path.exists(src):
            return None
        # 볼 때마다 다른 이름 - 앞서 연 복사본을 엑셀이 아직 잡고 있어도 막히지 않는다
        stamp = f"{self.now():%H%M%S}_{os.getpid()}"
        dst = os.path.join(tempfile.gettempdir(), f"{os.path.basename(src)[:-4]}_보기_{stamp}.csv")
        shutil.copyfile(src, dst)
        return dst

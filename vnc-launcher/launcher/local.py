"""이 PC에만 남기는 것: 즐겨찾기, 창 크기·위치, 마지막으로 본 그룹.

설비 목록(공유될 수 있음)과 달리 사람마다 다른 것이라 설정 폴더(core.app_dir)의 ui_state.json 에 둔다.
파일이 없거나 깨져 있으면 기본값으로 시작한다 - 이 파일 때문에 프로그램이 멈추면 안 된다.
"""
from __future__ import annotations

import json
import os
import re


class LocalState:
    def __init__(self, path: str):
        self.path = path
        self.favorites: list[str] = []   # 설비 id, 추가한 순서
        self.geometry = ""               # "폭x높이+x+y" (최대화하기 전의 크기)
        self.zoomed = False
        self.group = ""
        self.applied_pack = ""           # 이미 가져온 배포본 id (같은 배포본을 다시 묻지 않게)
        self.load()

    def _read(self) -> dict | None:
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _ids(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(v for v in value if isinstance(v, str) and v))

    def refresh_lists(self) -> None:
        """즐겨찾기 목록을 파일에서 다시 읽는다. 같은 PC에 창이 두 개 떠 있어도
        한쪽이 저장할 때 다른 쪽에서 바꾼 것을 지우지 않도록, 바꾸기 직전에 부른다."""
        data = self._read()
        if data is not None:
            self.favorites = self._ids(data.get("favorites"))

    def load(self) -> None:
        data = self._read()
        if data is None:
            return
        self.favorites = self._ids(data.get("favorites"))
        self.geometry = data.get("geometry") if isinstance(data.get("geometry"), str) else ""
        self.zoomed = data.get("zoomed") is True
        self.group = data.get("group") if isinstance(data.get("group"), str) else ""
        self.applied_pack = data.get("applied_pack") if isinstance(data.get("applied_pack"), str) else ""

    def save(self) -> bool:
        tmp = self.path + ".tmp"
        data = {"favorites": self.favorites, "geometry": self.geometry,
                "zoomed": self.zoomed, "group": self.group, "applied_pack": self.applied_pack}
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def is_favorite(self, eq_id: str) -> bool:
        return eq_id in self.favorites

    def set_favorite(self, ids, on: bool) -> int:
        """즐겨찾기에 넣거나 뺀다. 바뀐 수를 돌려준다."""
        self.refresh_lists()
        changed = 0
        for eq_id in ids:
            if on and eq_id not in self.favorites:
                self.favorites.append(eq_id)
                changed += 1
            elif not on and eq_id in self.favorites:
                self.favorites.remove(eq_id)
                changed += 1
        return changed

    def forget(self, ids) -> None:
        """삭제된 설비를 즐겨찾기에서 뺀다."""
        self.refresh_lists()
        ids = set(ids)
        self.favorites = [i for i in self.favorites if i not in ids]


def quick_ids(favorites, existing) -> list[str]:
    """맨 위 "즐겨찾기" 줄에 놓을 설비 id (지금 목록에 있는 것만, 넣은 순서대로).

    접속할 때마다 바뀌는 것(예전의 최근 접속)은 넣지 않는다 - 접속할 때 화면 배치가 흔들리지 않게."""
    return [i for i in favorites if i in existing]


_GEOM = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")


def fit_geometry(geometry: str, screen: tuple[int, int, int, int],
                 min_size: tuple[int, int]) -> str | None:
    """저장해 둔 창 위치를 지금 화면 안으로 맞춘다 (모니터를 뺐거나 해상도가 바뀐 경우).

    screen = (x, y, 폭, 높이): 모든 모니터를 합친 영역. 형식이 틀리면 None.
    """
    m = _GEOM.match(geometry or "")
    if not m:
        return None
    w, h, x, y = (int(v) for v in m.groups())
    sx, sy, sw, sh = screen
    w = max(min_size[0], min(w, sw))
    h = max(min_size[1], min(h, sh))
    x = max(sx, min(x, sx + sw - w))
    y = max(sy, min(y, sy + sh - h))
    return f"{w}x{h}+{x}+{y}"

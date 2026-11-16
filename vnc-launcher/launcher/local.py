"""이 PC에만 남기는 것: 즐겨찾기, 최근 접속, 창 크기·위치, 마지막으로 본 그룹.

설비 목록(공유될 수 있음)과 달리 사람마다 다른 것이라 프로그램 폴더의 ui_state.json 에 둔다.
파일이 없거나 깨져 있으면 기본값으로 시작한다 - 이 파일 때문에 프로그램이 멈추면 안 된다.
"""
from __future__ import annotations

import json
import os
import re

RECENT_MAX = 12


class LocalState:
    def __init__(self, path: str):
        self.path = path
        self.favorites: list[str] = []   # 설비 id, 추가한 순서
        self.recent: list[str] = []      # 설비 id, 최근 것이 앞
        self.geometry = ""               # "폭x높이+x+y" (최대화하기 전의 크기)
        self.zoomed = False
        self.group = ""
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return

        def ids(value) -> list[str]:
            if not isinstance(value, list):
                return []
            return list(dict.fromkeys(v for v in value if isinstance(v, str) and v))

        self.favorites = ids(data.get("favorites"))
        self.recent = ids(data.get("recent"))[:RECENT_MAX]
        self.geometry = data.get("geometry") if isinstance(data.get("geometry"), str) else ""
        self.zoomed = data.get("zoomed") is True
        self.group = data.get("group") if isinstance(data.get("group"), str) else ""

    def save(self) -> bool:
        tmp = self.path + ".tmp"
        data = {"favorites": self.favorites, "recent": self.recent, "geometry": self.geometry,
                "zoomed": self.zoomed, "group": self.group}
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
        changed = 0
        for eq_id in ids:
            if on and eq_id not in self.favorites:
                self.favorites.append(eq_id)
                changed += 1
            elif not on and eq_id in self.favorites:
                self.favorites.remove(eq_id)
                changed += 1
        return changed

    def touch(self, eq_id: str) -> None:
        """방금 접속한 설비를 최근 목록 맨 앞으로."""
        self.recent = ([eq_id] + [r for r in self.recent if r != eq_id])[:RECENT_MAX]

    def forget(self, ids) -> None:
        """삭제된 설비를 즐겨찾기·최근 목록에서 뺀다."""
        ids = set(ids)
        self.favorites = [i for i in self.favorites if i not in ids]
        self.recent = [i for i in self.recent if i not in ids]


def quick_ids(favorites, recent, existing, cols: int) -> tuple[list[str], int]:
    """맨 위 "즐겨찾기 · 최근" 줄에 놓을 설비 id 와 그중 즐겨찾기 수.

    즐겨찾기는 모두 보이고(여러 줄이 될 수 있다), 최근 접속은 마지막 줄의 남은 칸만 채운다.
    그래서 이 줄 때문에 늘어나는 높이는 최근 접속만 있을 때 한 줄이다.
    """
    favs = [i for i in favorites if i in existing]
    rest = [i for i in recent if i in existing and i not in favs]
    if not favs and not rest:
        return [], 0
    cols = max(1, cols)
    room = max(cols, -(-len(favs) // cols) * cols)
    return (favs + rest)[:room], len(favs)


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

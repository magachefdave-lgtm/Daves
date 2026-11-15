"""tkinter 화면 - 설비를 카드형 버튼으로 보여 준다."""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import APP_NAME, APP_VERSION, secret, wintitle
from .core import (OS_CHOICES, OS_WINDOWS, VIEWERS, Config, Equipment,
                   EquipmentStore, StoreError, app_dir, build_command,
                   detect_viewer, equipment_file, export_csv, has_share, is_reachable,
                   load_config, natural_key, save_config, share_path)

ALL_GROUPS = "전체"
NO_GROUP = "그룹 없음"

# 색 - 두 가지 원칙을 같이 쓴다.
# 1) 산업용 모니터링 화면(ISA-101 계열): 바탕과 정상 상태는 무채색, 색은 "응답 없음"(빨강)과
#    "접속 중"(파랑)에만 쓴다.
# 2) 질감: 은회색 바탕 위에 받침판(그룹)을 깔고 그 위에 흰 버튼을 띄운다. 테두리는 머리카락 굵기,
#    그림자는 옅게, 모서리는 받침판과 버튼이 같은 중심을 갖도록(받침판 16 = 여백 6 + 버튼 10).
BG = "#eceef1"          # 은회색 바탕
TRAY = "#e2e5e9"        # 그룹 받침판
TRAY_LINE = "#d5d9df"
CARD = "#ffffff"        # 대화상자, 메뉴
TILE = "#fdfdfe"        # 설비 버튼 (정상)
BORDER = "#dfe3e8"      # 버튼 테두리 (머리카락 굵기)
SHADOW = "#cfd4db"      # 버튼 아래 옅은 그림자
SHADOW_DEEP = "#b3bac4"
TEXT = "#171c22"
SUB = "#68727e"
HEAD = "#1a2028"        # 떠 있는 위쪽 바
HEAD_FIELD = "#2a323c"
HEAD_HOVER = "#3a4450"
HEAD_TEXT = "#f4f5f7"
HEAD_SUB = "#98a2ae"
ACCENT = "#1f5fd6"      # 접속 중
ACCENT_DARK = "#184cad"
TRYING_BG = "#e7effd"   # 접속 시도 중
TRYING_LINE = "#8fb2f0"
SOFT = "#e6e9ed"
SOFT_HOVER = "#d5dae0"
ALERT = "#c0271d"       # 응답 없음
ALERT_DARK = "#a01f17"
ALERT_BG = "#fbe6e3"
ALERT_LINE = "#eab5b0"
ALERT_TEXT = "#8a1c14"
NORMAL = "#5b8a72"      # 정상: 채도를 낮춘 녹색 점
IDLE = "#98a2ae"
STATUS_WORD = {True: "접속 가능", False: "응답 없음", "busy": "확인 중"}
RADIUS = 10             # 설비 버튼 모서리
TRAY_PAD = 6            # 받침판 안쪽 여백
TRAY_RADIUS = RADIUS + TRAY_PAD


def mix(a: str, b: str, t: float) -> str:
    """색 a 에서 b 로 t(0~1)만큼 섞는다."""
    t = max(0.0, min(1.0, t))
    pa, pb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def ease(t: float) -> float:
    """빨리 출발해 천천히 멎는 곡선 - 물건이 무게를 갖고 자리에 앉는 느낌."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 4


# 설비 버튼 크기: 화면에 다 들어오도록 TILE_W_WIDE 에서 TILE_W_NARROW 까지 줄인다.
TILE_W_WIDE, TILE_W_NARROW, TILE_H, GAP, PAD, GROUP_GAP = 176, 118, 36, 6, 16, 8
HINT = "클릭 = VNC 접속   ·   폴더 아이콘 = 공유폴더   ·   오른쪽 클릭 = 수정 / 삭제 / 그룹 이동"


def launch(cmd: list[str]) -> subprocess.Popen:
    # 콘솔 없는 exe에서는 표준 핸들이 유효하지 않아 넘겨주지 않으면 WinError 6이 난다.
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True)


def round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    # 꼭짓점 앞뒤 점을 두 번씩 넣어야 변이 곧게 그려진다.
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
           x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class PillButton(tk.Canvas):
    """알약 모양 버튼. 마우스를 올리면 색이 부드럽게 바뀌고, 누르면 살짝 내려앉는다."""

    def __init__(self, parent, text, command, font, colors, bg):
        super().__init__(parent, height=30, highlightthickness=0, bg=bg, cursor="hand2")
        self.font, self.colors, self.command, self.enabled = font, colors, command, True
        self.now = colors[0]
        self.job = None
        self.down = False
        self.set_text(text)
        self.bind("<Enter>", lambda _e: self.enabled and self.fade(self.colors[1]))
        self.bind("<Leave>", lambda _e: self.enabled and self.fade(self.colors[0]))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def set_text(self, text: str):
        self.text = text
        w = self.font.measure(text) + 30
        self.configure(width=w)
        self.delete("all")
        self.shape = round_rect(self, 0, 0, w - 1, 29, 15, fill=self.now, outline="")
        self.label = self.create_text(w / 2, 15, text=text, font=self.font,
                                      fill=self.colors[2] if self.enabled else HEAD_SUB)
        self.down = False

    def fade(self, to: str, ms: int = 160):
        if self.job:
            self.after_cancel(self.job)
        start, t0 = self.now, time.monotonic()

        def step():
            t = (time.monotonic() - t0) * 1000 / ms
            self.now = mix(start, to, ease(t))
            self.itemconfigure(self.shape, fill=self.now)
            self.job = self.after(16, step) if t < 1 else None

        step()

    def _press(self, _event):
        if self.enabled and not self.down:
            self.move("all", 0, 1)
            self.down = True

    def _release(self, event):
        if self.down:
            self.move("all", 0, -1)
            self.down = False
        if self.enabled and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def set_enabled(self, flag: bool, text: str | None = None):
        self.enabled = flag
        self.configure(cursor="hand2" if flag else "")
        self.now = self.colors[0]
        self.set_text(text or self.text)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config_path = os.path.join(app_dir(), "config.ini")
        self.cfg = Config()
        self.items: list[Equipment] = []
        self.status: dict[str, object] = {}
        self.group = ALL_GROUPS
        self.hover = (None, None)   # (설비 id, 영역)
        self.pressed = (None, None)
        # 떠 있는 뷰어: {id, name, proc, started, connected}. connected=False 는 "접속 시도 중"
        self.sessions: list[dict] = []
        self.boxes: dict[str, tuple] = {}  # 접속 시도 중인 설비 버튼의 위치 (움직이는 막대용)
        self.base: dict[str, tuple] = {}   # 설비 버튼의 평소 색 (마우스가 떠날 때 되돌린다)
        self.pool = ThreadPoolExecutor(max_workers=16)
        self.results: queue.Queue = queue.Queue()
        self.pending = 0
        self.seen_mtime = 0.0
        self.scrollable = False
        self.only_down = False   # 응답 없는 설비만 보기
        self.gen = 0             # 화면을 다시 그릴 때마다 1 증가
        self.sunk = None         # 눌려서 내려앉은 설비 버튼
        self.reveal = 0.0        # 처음 나타나는 정도 (1 이상 = 다 나타남)
        self._draw_job = self._toast_job = None
        self.store = EquipmentStore("")

        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.geometry("1180x720")
        root.minsize(760, 420)
        root.configure(bg=BG)
        self._fonts()
        self._style()
        self._build()

        first_run = not os.path.exists(self.config_path)
        try:
            self.cfg = load_config(self.config_path)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        if first_run:
            found = detect_viewer()
            if found:
                self.cfg.viewer_type, self.cfg.viewer_path = found
            self._save_config()
        self.passwords = secret.PasswordStore(os.path.join(app_dir(), "passwords.json"))
        self._open_store()
        self.reload()
        self.root.after(60, self._reveal)
        if self.cfg.check_on_start:
            self.check_status()
        self.root.after(300, self._poll_results)
        self.root.after(5000, self._watch_file)
        self.root.after(1000, self._tick_sessions)

    # ------------------------------------------------------------ 화면 ---

    def _fonts(self):
        installed = set(tkfont.families(self.root))
        family = next((f for f in ("Pretendard", "Pretendard Variable", "맑은 고딕", "Malgun Gothic")
                       if f in installed), tkfont.nametofont("TkDefaultFont").actual("family"))
        f = lambda size, weight="normal": tkfont.Font(family=family, size=size, weight=weight)
        self.f_title, self.f_name = f(13, "bold"), f(10, "bold")
        self.f_body, self.f_small, self.f_group = f(10), f(9), f(10, "bold")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=family, size=10)

    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=CARD, foreground=TEXT, bordercolor=BORDER,
                     lightcolor=CARD, darkcolor=CARD, troughcolor=BG, focuscolor=ACCENT)
        st.configure("TButton", background=SOFT, bordercolor=SOFT, padding=(14, 6), relief="flat")
        st.map("TButton", background=[("active", SOFT_HOVER)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT)
        st.map("Accent.TButton", background=[("active", ACCENT_DARK)])
        st.configure("TEntry", fieldbackground=CARD, padding=5)
        st.configure("TCombobox", fieldbackground=CARD, background=CARD, padding=5, arrowcolor=SUB)
        st.map("TCombobox", fieldbackground=[("readonly", CARD)], bordercolor=[("focus", ACCENT)])
        st.map("TEntry", bordercolor=[("focus", ACCENT)],
               fieldbackground=[("disabled", BG), ("readonly", BG)])
        st.configure("TCheckbutton", background=CARD)
        st.configure("Vertical.TScrollbar", background="#cfd6df", troughcolor=BG, bordercolor=BG,
                     arrowcolor=BG, relief="flat", width=10)
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    def _build(self):
        root = self.root
        # 위쪽 바: 창 가장자리에 붙이지 않고, 둥근 알약 모양으로 띄운다.
        head = tk.Canvas(root, bg=BG, height=64, highlightthickness=0)
        head.pack(fill="x")
        row1 = tk.Frame(head, bg=HEAD)
        island = head.create_window(PAD + 20, 33, anchor="w", window=row1)

        def lay_island(event):
            head.delete("island")
            for grow, color in ((3, mix(BG, "#000000", 0.03)), (2, mix(BG, "#000000", 0.06)),
                                (1, mix(BG, "#000000", 0.10))):   # 옅게 퍼지는 그림자
                round_rect(head, PAD - grow, 11 - grow + 3, event.width - PAD + grow, 55 + grow + 3,
                           22 + grow, fill=color, outline="", tags="island")
            round_rect(head, PAD, 11, event.width - PAD, 55, 22, fill=HEAD, outline="", tags="island")
            head.tag_lower("island")
            head.itemconfigure(island, width=event.width - 2 * PAD - 32)

        head.bind("<Configure>", lay_island)
        quiet = (HEAD_FIELD, HEAD_HOVER, HEAD_TEXT)
        self.more_btn = PillButton(row1, "더보기  ▾", self._show_more, self.f_body, quiet, HEAD)
        self.more_btn.pack(side="right")
        self.check_btn = PillButton(row1, "상태 확인", self.check_status, self.f_body, quiet, HEAD)
        self.check_btn.pack(side="right", padx=6)
        PillButton(row1, "＋  설비 추가", self.add, self.f_group, (HEAD_TEXT, "#ffffff", HEAD),
                   HEAD).pack(side="right")
        tk.Label(row1, text=APP_NAME, font=self.f_title, bg=HEAD, fg=HEAD_TEXT).pack(side="left")
        box = tk.Canvas(row1, width=232, height=30, bg=HEAD, highlightthickness=0)
        box.pack(side="left", padx=(18, 14))
        round_rect(box, 0, 0, 231, 29, 15, fill=HEAD_FIELD, outline="")
        box.create_text(16, 15, anchor="w", text="검색", font=self.f_small, fill=HEAD_SUB)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.draw())
        self.search = tk.Entry(box, textvariable=self.search_var, relief="flat", bg=HEAD_FIELD,
                               fg=HEAD_TEXT, font=self.f_body, insertbackground=HEAD_TEXT,
                               highlightthickness=0, bd=0)
        box.create_window(48, 15, anchor="w", window=self.search, width=168)
        box.bind("<Button-1>", lambda _e: self.search.focus_set())
        self.search.focus_set()
        self.summary = tk.Label(row1, font=self.f_small, bg=HEAD, fg=HEAD_SUB)
        self.summary.pack(side="left")
        # 응답 없는 설비 수 - 있을 때만 빨갛게 나타나고, 누르면 그 설비만 보인다.
        self.alert_btn = PillButton(row1, "", self.toggle_only_down, self.f_group,
                                    (ALERT, ALERT_DARK, "#ffffff"), HEAD)

        # 그룹 줄. 오른쪽 끝에는 지금 VNC로 접속해 있는 설비가 나온다.
        self.chips = tk.Canvas(root, bg=BG, height=28, highlightthickness=0)
        self.chips.pack(fill="x", padx=PAD, pady=(2, 6))
        self.chips.bind("<Configure>", lambda _e: self._draw_chips())
        self.chips.bind("<Button-1>", self._on_chip)

        foot = tk.Frame(root, bg=BG)
        foot.pack(side="bottom", fill="x")
        for mark, color, word in (("▮", ACCENT, "접속 중"), ("■", ALERT, "응답 없음"),
                                  ("○", IDLE, "미확인"), ("●", NORMAL, "정상")):
            tk.Label(foot, text=word, bg=BG, fg=SUB, font=self.f_small).pack(side="right", padx=(0, 14))
            tk.Label(foot, text=mark, bg=BG, fg=color, font=self.f_small).pack(side="right", padx=(0, 3))
        self.foot_var = tk.StringVar(value=HINT)
        tk.Label(foot, textvariable=self.foot_var, bg=BG, fg=SUB, font=self.f_small,
                 anchor="w").pack(side="left", fill="x", expand=True, padx=PAD + 4, pady=(2, 8))

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)
        self.canvas = c = tk.Canvas(body, bg=BG, highlightthickness=0)
        self.scroll = ttk.Scrollbar(body, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=self.scroll.set)
        c.pack(side="left", fill="both", expand=True)
        c.bind("<Configure>", lambda _e: self.draw_later())
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda _e: self._set_hover((None, None)))
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<ButtonRelease-1>", self._on_click)
        c.bind("<Button-3>", self._on_right)
        c.bind("<MouseWheel>", lambda e: self._wheel(-1 if e.delta > 0 else 1))
        c.bind("<Button-4>", lambda _e: self._wheel(-1))
        c.bind("<Button-5>", lambda _e: self._wheel(1))
        c.configure(yscrollincrement=TILE_H + GAP)

        root.bind("<F5>", lambda _e: (self.reload(), self.check_status()))
        root.bind("<Control-f>", lambda _e: self.search.focus_set())
        self.search.bind("<Escape>", lambda _e: self.search_var.set(""))
        self.search.bind("<Return>", self._on_enter)

        self.menu_opts = dict(tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                              activeforeground="#ffffff", relief="flat", bd=1)
        self.menu = tk.Menu(root, **self.menu_opts)
        self.move_menu = tk.Menu(self.menu, **self.menu_opts)
        self.more_menu = tk.Menu(root, **self.menu_opts)
        self.more_menu.add_command(label="그룹 관리", command=self.open_groups)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="CSV 가져오기 (설비 · 비밀번호)", command=self.import_csv)
        self.more_menu.add_command(label="CSV 내보내기", command=self.export_csv)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="설정", command=self.open_settings)

    def _show_more(self):
        b = self.more_btn
        self.more_menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 2)

    def _wheel(self, step: int):
        if self.scrollable:
            self.canvas.yview_scroll(step, "units")

    # ------------------------------------------------------ 그룹 칩 ---

    def _groups(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for e in self.items:
            counts[e.group] = counts.get(e.group, 0) + 1
        out = [(ALL_GROUPS, len(self.items))] + [(g, counts.get(g, 0)) for g in self.store.groups]
        if counts.get("") and self.store.groups:
            out.append((NO_GROUP, counts[""]))
        return out

    def _session_names(self, connected: bool) -> list[str]:
        return list(dict.fromkeys(s["name"] for s in self.sessions if s["connected"] == connected))

    @staticmethod
    def _join(names: list[str]) -> str:
        return ",  ".join(names[:3]) + (f"  외 {len(names) - 3}대" if len(names) > 3 else "")

    def _draw_chips(self):
        c = self.chips
        c.delete("all")
        width = max(c.winfo_width(), 200)
        # 오른쪽 끝: 접속 시도 중(옅은 파랑) / 접속 중(파랑) 인 설비. 없으면 흐린 글자만.
        trying, active = self._session_names(False), self._session_names(True)
        right = width
        for names, word, fill, line, ink in ((active, "접속 중", ACCENT, ACCENT, "#ffffff"),
                                             (trying, "접속 시도 중…", TRYING_BG, TRYING_LINE, ACCENT_DARK)):
            if not names:
                continue
            text = f"{word}   {self._join(names)}"
            w = self.f_group.measure(text) + 44
            round_rect(c, right - w, 0, right - 1, 25, 13, fill=fill, outline=line)
            c.create_rectangle(right - w + 14, 7, right - w + 18, 19, fill=ink, outline="")
            c.create_text(right - w + 28, 13, anchor="w", text=text, font=self.f_group, fill=ink)
            right -= w + 6
        if right == width:
            idle = "접속 중인 설비 없음"
            right -= self.f_small.measure(idle) + 8
            c.create_text(width - 4, 13, anchor="e", text=idle, font=self.f_small, fill=SUB)
        aw = width - right
        room = width - aw - 16
        x = y = 0
        chips = [(f"chip:{name}", f"{name}  {count}", name == self.group)
                 for name, count in self._groups()]
        chips.append(("manage", "＋ 그룹 관리", None))
        for tag, label, on in chips:
            w = self.f_small.measure(label) + 26
            if x and x + w > (room if y == 0 else width):
                x, y = 0, y + 31
            if on is None:   # 그룹 관리: 글자만
                c.create_rectangle(x, y, x + w, y + 26, fill=BG, outline="", tags=(tag,))
                color = SUB
            elif on:         # 고른 그룹: 진한 무채색 (파랑은 "접속 중"에만 쓴다)
                round_rect(c, x, y, x + w, y + 26, 13, fill=HEAD, outline="", tags=(tag,))
                color = "#ffffff"
            else:
                round_rect(c, x, y, x + w - 1, y + 25, 13, fill=TILE, outline=BORDER, tags=(tag,))
                color = TEXT
            c.create_text(x + w / 2, y + 13, text=label, font=self.f_small, fill=color, tags=(tag,))
            x += w + 6
        c.configure(height=y + 27)

    def _on_chip(self, _event):
        for tag in self.chips.gettags("current"):
            if tag == "manage":
                self.open_groups()
            elif tag.startswith("chip:"):
                self.group = tag[5:]
                self._draw_chips()
                self.canvas.yview_moveto(0)
                self.draw()

    # ------------------------------------------------------ 설비 버튼 ---

    def visible(self) -> list[Equipment]:
        words = self.search_var.get().casefold().split()
        out = []
        for e in self.items:
            if self.group != ALL_GROUPS and (e.group or NO_GROUP) != self.group:
                continue
            if self.only_down and self.status.get(e.id) is not False:
                continue
            hay = " ".join((e.name, e.group, e.host, e.os, e.memo)).casefold()
            if all(w in hay for w in words):
                out.append(e)
        out.sort(key=lambda e: natural_key(e.name))
        return out

    def draw_later(self):
        if self._draw_job:
            self.root.after_cancel(self._draw_job)
        self._draw_job = self.root.after(30, self.draw)

    def _fit(self, text: str, font: tkfont.Font, width: float) -> str:
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def draw(self):
        self._draw_job = None
        c = self.canvas
        c.delete("all")
        self.gen += 1                 # 진행 중이던 색 전환은 여기서 끝난다
        self.hover = (None, None)
        self.base = {}
        self.boxes = {}
        self.sunk = None
        width, height = max(c.winfo_width(), 300), max(c.winfo_height(), 100)
        shown = self.visible()
        down = sum(1 for e in self.items if self.status.get(e.id) is False)
        self.summary["text"] = f"전체 {len(self.items)}대"
        if down or self.only_down:
            text = f"응답 없음 {down}대만 보는 중   ✕" if self.only_down else f"응답 없음  {down}대"
            if self.alert_btn.text != text:
                self.alert_btn.set_text(text)
            self.alert_btn.pack(side="left", padx=(12, 0))
        else:
            self.alert_btn.pack_forget()
        self._foot()

        if not shown:
            if self.only_down and self.items:
                title, hint = "응답 없는 설비가 없습니다", "위의 빨간 표시를 다시 누르면 전체 설비가 보입니다"
            elif self.items:
                title, hint = "조건에 맞는 설비가 없습니다", "검색어를 지우거나 다른 그룹을 선택하세요"
            else:
                title, hint = "등록된 설비가 없습니다", "[＋ 설비 추가] 또는 [더보기 > CSV 가져오기]로 설비를 등록하세요"
            c.create_text(width / 2, 110, text=title, font=self.f_title, fill=SUB)
            c.create_text(width / 2, 140, text=hint, font=self.f_body, fill=SUB)
            self._set_scroll(0, height)
            return

        sections: dict[str, list[Equipment]] = {}
        for e in shown:
            sections.setdefault(e.group, []).append(e)
        names = [g for g in self.store.groups if g in sections] + ([""] if "" in sections else [])
        labels = bool(names and names[0])
        # 그룹 이름은 받침판 왼쪽 칸에 둔다 - 제목 줄을 따로 쓰지 않아 세로 공간을 아낀다.
        label_w = 0
        if labels:
            label_w = min(140, max(self.f_group.measure(g or NO_GROUP) for g in names) + 26)
        avail = width - 2 * PAD - 2 * TRAY_PAD - label_w
        cols_for = lambda tile_w: max(1, int((avail + GAP) // (tile_w + GAP)))
        top = 2

        def total(cols):
            rows = [(len(sections[g]) + cols - 1) // cols for g in names]
            return (sum(r * (TILE_H + GAP) - GAP + 2 * TRAY_PAD for r in rows)
                    + GROUP_GAP * (len(names) - 1) + top + 6)

        # 넓은 버튼부터 시작해, 스크롤 없이 들어올 때까지 한 줄의 버튼 수를 늘린다.
        # 설비명 대부분(90%)이 잘리지 않는 폭까지만 줄인다. 유난히 긴 이름 몇 개는 … 로 줄인다.
        widths = sorted(self.f_name.measure(e.name) for e in shown)
        need = widths[int((len(widths) - 1) * 0.9)] + 25 + 40
        narrow = min(TILE_W_WIDE, max(TILE_W_NARROW, need))
        cols, most = cols_for(TILE_W_WIDE), cols_for(narrow)
        while cols < most and total(cols) > height:
            cols += 1
        tile_w = (avail - GAP * (cols - 1)) / cols
        x0 = PAD + TRAY_PAD + label_w
        y = top
        for n, g in enumerate(names):
            group_items = sections[g]
            rows = (len(group_items) + cols - 1) // cols
            tray_h = rows * (TILE_H + GAP) - GAP + 2 * TRAY_PAD
            # 처음 켤 때: 그룹이 위에서부터 차례로 떠오르며 나타난다.
            a = ease((self.reveal - 0.09 * min(n, 6)) / 0.45)
            lift = (1 - a) * 10
            round_rect(c, PAD, y + lift, width - PAD, y + tray_h + lift, TRAY_RADIUS,
                       fill=mix(BG, TRAY, a), outline=mix(BG, TRAY_LINE, a))
            if labels:
                name = g or NO_GROUP
                ty = y + lift + TRAY_PAD + TILE_H / 2
                c.create_text(PAD + 16, ty - (7 if rows > 1 else 0), anchor="w", font=self.f_group,
                              fill=mix(TRAY, TEXT, a), text=self._fit(name, self.f_group, label_w - 22))
                if rows > 1:
                    c.create_text(PAD + 16, ty + 11, anchor="w", font=self.f_small, fill=mix(TRAY, SUB, a),
                                  text=f"{len(group_items)}대")
            for i, e in enumerate(group_items):
                self._tile(e, x0 + (i % cols) * (tile_w + GAP),
                           y + lift + TRAY_PAD + (i // cols) * (TILE_H + GAP), tile_w, a)
            y += tray_h + GROUP_GAP
        self._set_scroll(y - GROUP_GAP + 6, height)

    def _reveal(self, t0: float | None = None):
        """처음 켤 때 한 번, 화면이 0.7초에 걸쳐 차례로 나타난다."""
        t0 = t0 or time.monotonic()
        self.reveal = (time.monotonic() - t0) / 0.7
        if self.reveal >= 1:
            self.reveal = 2.0
            self.draw()
            return
        self.draw()
        self.root.after(16, lambda: self._reveal(t0))

    def _set_scroll(self, content_h: float, height: int):
        """내용이 화면보다 길 때만 스크롤 막대를 보인다."""
        self.scrollable = content_h > height
        self.canvas.configure(scrollregion=(0, 0, 1, max(content_h, height)))
        if self.scrollable:
            self.scroll.pack(side="right", fill="y")
        else:
            self.scroll.pack_forget()
            self.canvas.yview_moveto(0)

    def _share_on(self, e: Equipment) -> bool:
        return self.cfg.show_share and has_share(e)

    def _tile(self, e: Equipment, x: float, y: float, w: float, a: float = 1.0):
        c = self.canvas
        st = self.status.get(e.id)
        tid = f"id:{e.id}"
        body = (tid, "zone:body")
        mid = y + TILE_H / 2
        mine = [s for s in self.sessions if s["id"] == e.id]
        active = any(s["connected"] for s in mine)
        trying = bool(mine) and not active
        # (바탕, 테두리, 글자, 아이콘, 그림자) - 정상은 무채색, 색은 이상(빨강)과 접속(파랑)에만
        if trying:
            look = (TRYING_BG, TRYING_LINE, ACCENT_DARK, ACCENT, mix(SHADOW, ACCENT, 0.2))
        elif active:
            look = (ACCENT, ACCENT_DARK, "#ffffff", "#ffffff", mix(SHADOW, ACCENT, 0.35))
        elif st is False:
            look = (ALERT_BG, ALERT_LINE, ALERT_TEXT, ALERT_TEXT, mix(SHADOW, ALERT, 0.15))
        else:
            look = (TILE, BORDER, TEXT, IDLE, SHADOW)
        if a < 1:   # 나타나는 중: 받침판 색에서 제 색으로
            look = tuple(mix(TRAY, col, a) for col in look)
        self.base[e.id] = look
        fill, outline, ink, icon, shadow = look
        # 그림자는 버튼과 따로 둔다 - 버튼만 뜨고 가라앉아 깊이가 생긴다.
        round_rect(c, x + 1, y + 2, x + w - 1, y + TILE_H + 2, RADIUS, fill=shadow, outline="",
                   tags=(f"shadow:{e.id}",))
        round_rect(c, x, y, x + w, y + TILE_H, RADIUS, fill=fill, outline=outline, width=1,
                   tags=body + (f"card:{e.id}",))
        # 상태 표시: 색을 못 가려도 알 수 있게 모양도 다르게 한다.
        if trying:   # 접속 시도 중: 버튼 아래쪽을 막대가 오간다 (_sweep 이 움직인다)
            c.create_oval(x + 10, mid - 4, x + 18, mid + 4, fill=fill, outline=ACCENT, width=2, tags=body)
            self.boxes[e.id] = (x, y, w)
            c.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="", tags=body + (f"sweep:{e.id}",))
            self._place_sweep(e.id)
        elif active:
            c.create_rectangle(x + 12, mid - 6, x + 16, mid + 6, fill=ink, outline="", tags=body)
        elif st is False:
            c.create_rectangle(x + 10, mid - 4, x + 18, mid + 4, fill=mix(TRAY, ALERT, a), outline="", tags=body)
        elif st is True:
            c.create_oval(x + 10, mid - 4, x + 18, mid + 4, fill=mix(TRAY, NORMAL, a), outline="", tags=body)
        else:
            c.create_oval(x + 10, mid - 4, x + 18, mid + 4, fill=fill, outline=icon, width=2, tags=body)
        right = x + w - 6
        if self._share_on(e):   # 공유폴더 버튼: 평소엔 가는 선 아이콘만, 마우스를 올리면 동그란 버튼
            share = (tid, "zone:share")
            c.create_oval(right - 26, mid - 13, right, mid + 13, fill=fill, outline="",
                          tags=share + (f"btn:{e.id}",))
            cx = right - 13
            c.create_polygon(cx - 6, mid - 5, cx - 2, mid - 5, cx, mid - 3, cx + 6, mid - 3,
                             cx + 6, mid + 5, cx - 6, mid + 5, fill=fill, outline=icon,
                             tags=share + (f"ico:{e.id}",))
            right -= 30
        c.create_text(x + 26, mid, anchor="w", font=self.f_name, tags=body, fill=ink,
                      text=self._fit(e.name, self.f_name, right - (x + 26)))

    # ------------------------------------------------- 마우스, 키보드 ---

    def _target(self) -> tuple[str | None, str | None]:
        eq_id = zone = None
        for tag in self.canvas.gettags("current"):
            if tag.startswith("id:"):
                eq_id = tag[3:]
            elif tag.startswith("zone:"):
                zone = tag[5:]
        return eq_id, zone

    def _by_id(self, eq_id) -> Equipment | None:
        return next((e for e in self.items if e.id == eq_id), None)

    def _fade(self, tag: str, option: str, start: str, to: str, ms: int = 170):
        """캔버스 항목의 색을 부드럽게 바꾼다. 화면을 다시 그리면(gen 이 바뀌면) 멈춘다."""
        gen, t0, c = self.gen, time.monotonic(), self.canvas

        def step():
            if gen != self.gen:
                return
            t = (time.monotonic() - t0) * 1000 / ms
            c.itemconfigure(tag, **{option: mix(start, to, ease(t))})
            if t < 1:
                self.root.after(16, step)

        step()

    def _set_hover(self, new):
        old = self.hover
        if new == old:
            return
        c = self.canvas
        blank = (TILE, BORDER, TEXT, IDLE, SHADOW)
        if old[0]:
            fill, outline, _ink, icon, shadow = self.base.get(old[0], blank)
            c.itemconfigure(f"btn:{old[0]}", fill=fill)
            c.itemconfigure(f"ico:{old[0]}", fill=fill, outline=icon)
            if old[0] != new[0]:   # 버튼을 떠날 때: 제자리로 내려앉는다
                self._settle(None)
                c.move(f"id:{old[0]}", 0, 1)
                self._fade(f"card:{old[0]}", "outline", HEAD, outline)
                self._fade(f"shadow:{old[0]}", "fill", SHADOW_DEEP, shadow)
        if new[0]:
            fill, outline, _ink, icon, shadow = self.base.get(new[0], blank)
            active = fill == ACCENT
            if old[0] != new[0]:   # 버튼에 올라갈 때: 1px 떠오르고 그림자가 짙어진다
                c.move(f"id:{new[0]}", 0, -1)
                self._fade(f"card:{new[0]}", "outline", outline, "#ffffff" if active else HEAD)
                self._fade(f"shadow:{new[0]}", "fill", shadow, SHADOW_DEEP)
            # 상태 색은 그대로 둔다 - 마우스를 올려도 상태가 가려지지 않는다.
            if new[1] == "share":
                on = "#ffffff" if active else HEAD
                c.itemconfigure(f"btn:{new[0]}", fill=on)
                c.itemconfigure(f"ico:{new[0]}", fill=on, outline=ACCENT if active else "#ffffff")
            else:
                c.itemconfigure(f"ico:{new[0]}", outline="#ffffff" if active else TEXT)
        c.configure(cursor="hand2" if new[0] else "")
        self.hover = new
        self._foot()

    def _on_press(self, _event):
        """누르면 버튼이 그림자 위로 내려앉는다."""
        self.pressed = self.hover
        if self.hover[0] and not self.sunk:
            self.canvas.move(f"id:{self.hover[0]}", 0, 2)
            self.sunk = self.hover[0]

    def _settle(self, _event):
        if self.sunk:
            self.canvas.move(f"id:{self.sunk}", 0, -2)
            self.sunk = None

    def _foot(self):
        """아래 한 줄: 방금 한 일 > 마우스가 올라간 설비 정보 > 사용법."""
        if self._toast_job:
            return
        eq = self._by_id(self.hover[0])
        if not eq:
            self.foot_var.set(HINT)
        elif self.hover[1] == "share":
            self.foot_var.set(f"{eq.name}  공유폴더 열기:  {share_path(eq)}")
        else:
            st = self.status.get(eq.id)
            mine = [s for s in self.sessions if s["id"] == eq.id]
            word = STATUS_WORD.get(st, "상태 미확인")
            if mine:
                word = "접속 중" if any(s["connected"] for s in mine) else "접속 시도 중"
            parts = [eq.name, eq.host if eq.port == 5900 else f"{eq.host}:{eq.port}", eq.os,
                     eq.group or NO_GROUP, word, eq.memo]
            self.foot_var.set("   ·   ".join(p for p in parts if p))

    def _on_motion(self, _event):
        self._set_hover(self._target())

    def _on_click(self, _event):
        self._settle(None)
        eq_id, zone = self._target()
        if not eq_id or (eq_id, zone) != self.pressed:
            return
        eq = self._by_id(eq_id)
        if eq and zone == "share":
            self.open_share(eq)
        elif eq:
            self.connect(eq)

    def _on_right(self, event):
        eq = self._by_id(self._target()[0])
        if not eq:
            return
        m, mv = self.menu, self.move_menu
        m.delete(0, "end")
        mv.delete(0, "end")
        m.add_command(label=f"{eq.name}  VNC 접속", command=lambda: self.connect(eq))
        if self._share_on(eq):
            m.add_command(label="공유폴더 열기", command=lambda: self.open_share(eq))
        m.add_command(label="IP 복사", command=lambda: self.copy_ip(eq))
        m.add_separator()
        for g in self.store.groups:
            mv.add_command(label=("✓ " if g == eq.group else "    ") + g,
                           command=lambda g=g: self.move_to(eq, g))
        mv.add_command(label=("✓ " if not eq.group else "    ") + NO_GROUP,
                       command=lambda: self.move_to(eq, ""))
        mv.add_separator()
        mv.add_command(label="    새 그룹 만들기…", command=lambda: self.move_to_new(eq))
        m.add_cascade(label="그룹 이동", menu=mv)
        if eq.os == OS_WINDOWS and self.cfg.show_share:
            m.add_command(label="공유폴더 버튼 숨기기" if eq.share_enabled else "공유폴더 버튼 보이기",
                          command=lambda: self.toggle_share(eq))
        m.add_command(label="수정", command=lambda: self.edit(eq))
        m.add_command(label="삭제", command=lambda: self.delete(eq))
        m.tk_popup(event.x_root, event.y_root)

    def toggle_only_down(self):
        self.only_down = not self.only_down
        self.canvas.yview_moveto(0)
        self.draw()

    def _on_enter(self, _event):
        shown = self.visible()
        if len(shown) == 1:
            self.connect(shown[0])
        elif shown:
            self.toast(f"설비 {len(shown)}대가 보입니다. 한 대만 남도록 검색하면 Enter로 바로 접속합니다.")

    def toast(self, text: str):
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self.foot_var.set(text)
        self._toast_job = self.root.after(4000, self._toast_done)

    def _toast_done(self):
        self._toast_job = None
        self._foot()

    # ---------------------------------------------------------- 데이터 ---

    def _save_config(self) -> bool:
        try:
            save_config(self.cfg, self.config_path)
            return True
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
            return False

    def _open_store(self):
        self.store = EquipmentStore(equipment_file(self.cfg))

    def reload(self) -> bool:
        self.seen_mtime = self.store.mtime()
        try:
            self.items = self.store.load()
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
            return False
        if self.group not in [g for g, _n in self._groups()]:
            self.group = ALL_GROUPS
        self._draw_chips()
        self.draw()
        return True

    def _watch_file(self):
        # 다른 PC가 공유 목록을 바꾸면 따라간다.
        if self.store.mtime() != self.seen_mtime:
            self.reload()
        self.root.after(5000, self._watch_file)

    # ------------------------------------------------------------ 동작 ---

    def connect(self, eq: Equipment | None):
        if not eq:
            return
        try:
            pw = self.passwords.get(eq.id) if secret.AVAILABLE else ""
            proc = launch(build_command(self.cfg, eq, pw))
        except StoreError as e:
            messagebox.showwarning(APP_NAME, str(e))
        except OSError as e:
            messagebox.showerror(APP_NAME, (
                "VNC 뷰어를 실행할 수 없습니다.\n"
                f"{self.cfg.viewer_path}\n\n"
                "[더보기 > 설정]에서 뷰어 실행 파일 경로가 맞는지 확인하세요.\n"
                f"({e})"))
        else:
            first = not any(not s["connected"] for s in self.sessions)
            self.sessions.append({"id": eq.id, "name": eq.name, "proc": proc,
                                  "started": time.monotonic(), "connected": False})
            self._show_sessions()
            self.draw()
            if first:
                self._sweep()
            if secret.AVAILABLE and self.cfg.supports_password() and not pw:
                self.toast(f"{eq.name}: 저장된 비밀번호가 없어 뷰어가 비밀번호를 묻습니다. "
                           "(오른쪽 클릭 > 수정, 또는 CSV 가져오기로 저장)")
            else:
                self.toast(f"{eq.name} ({eq.host}) 접속 시도 중…")

    def _tick_sessions(self):
        """뷰어 상태를 살핀다: 창이 떴는지(접속 시도 중 -> 접속 중), 닫혔는지. 창 제목에 설비명도 붙인다."""
        changed = False
        alive = []
        for s in self.sessions:
            if s["proc"].poll() is not None:       # 뷰어가 닫힘 (접속 실패 포함)
                changed = True
                if not s["connected"]:
                    self.toast(f"{s['name']}: 접속하지 못했습니다. (뷰어가 바로 닫혔습니다)")
                continue
            alive.append(s)
            windows = 0
            try:
                windows = wintitle.retitle(s["proc"].pid, s["name"], rename=self.cfg.title_with_name)
            except Exception:   # 창을 못 살펴도 접속에는 지장이 없다
                pass
            if not s["connected"]:
                waited = time.monotonic() - s["started"]
                # 뷰어 창이 화면에 나타나면 시도가 끝난 것으로 본다. 창을 살필 수 없는 경우를
                # 위해 시간 제한도 둔다.
                if windows or waited > (20 if wintitle.AVAILABLE else 2.5):
                    s["connected"] = True
                    changed = True
        self.sessions = alive
        if changed:
            self._show_sessions()
            self.draw()
        trying = any(not s["connected"] for s in alive)
        self.root.after(250 if trying else 1000, self._tick_sessions)

    def _place_sweep(self, eq_id: str):
        """접속 시도 중인 버튼 아래쪽에서 좌우로 오가는 막대의 지금 위치."""
        box = self.boxes.get(eq_id)
        if not box:
            return
        x, y, w = box
        span, bar = w - 2 * RADIUS, 34
        t = (time.monotonic() % 1.4) / 0.7           # 0~2: 갔다가 돌아온다
        pos = ease(t if t <= 1 else 2 - t) * (span - bar)
        left = x + RADIUS + pos
        self.canvas.coords(f"sweep:{eq_id}", left, y + TILE_H - 4, left + bar, y + TILE_H - 2)

    def _sweep(self):
        if not any(not s["connected"] for s in self.sessions):
            return
        for eq_id in list(self.boxes):
            self._place_sweep(eq_id)
        self.root.after(30, self._sweep)

    def _show_sessions(self):
        self._draw_chips()

    def toggle_share(self, eq: Equipment):
        eq.share_enabled = not eq.share_enabled
        try:
            self.store.update(eq)
        except StoreError as e:
            messagebox.showwarning(APP_NAME, str(e))
        else:
            self.toast(f"{eq.name} 공유폴더 버튼을 {'보이게' if eq.share_enabled else '숨기게'} 했습니다.")
        self.reload()

    def open_share(self, eq: Equipment | None):
        if not eq or not self._share_on(eq):
            return
        path = share_path(eq)
        if sys.platform != "win32":
            messagebox.showinfo(APP_NAME, f"Windows에서만 열 수 있습니다.\n{path}")
            return
        try:
            # 탐색기에 맡긴다 - 설비가 꺼져 있어도 이 프로그램이 멈추지 않고,
            # 계정/비밀번호가 필요하면 Windows가 직접 묻는다.
            launch(["explorer.exe", path])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"탐색기를 실행할 수 없습니다.\n{path}\n({e})")
        else:
            self.toast(f"{eq.name} 공유폴더를 엽니다: {path}")

    def copy_ip(self, eq: Equipment | None):
        if eq:
            self.root.clipboard_clear()
            self.root.clipboard_append(eq.host)
            self.toast(f"IP를 복사했습니다: {eq.host}")

    def move_to(self, eq: Equipment, group: str):
        try:
            moved = self.store.move([eq.id], group)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        else:
            if moved:
                self.toast(f"{eq.name} 설비를 '{group or NO_GROUP}'(으)로 옮겼습니다.")
        self.reload()

    def move_to_new(self, eq: Equipment):
        name = ask_group_name(self.root, "새 그룹 만들기", "", self.store.groups)
        if name:
            self.move_to(eq, name)

    def open_groups(self):
        dlg = GroupDialog(self.root, self.store.groups, self.items)

        def save(groups, assign):
            try:
                self.store.apply_groups(groups, assign)
            except StoreError as e:
                messagebox.showerror(APP_NAME, str(e), parent=dlg.top)
                self.reload()
                return False
            self.reload()
            self.toast("그룹을 저장했습니다.")
            return True

        dlg.on_save = save

    def add(self):
        group = "" if self.group in (ALL_GROUPS, NO_GROUP) else self.group
        self._edit_dialog(Equipment(name="", host="", group=group), is_new=True)

    def edit(self, eq: Equipment | None):
        if eq:
            self._edit_dialog(eq, is_new=False)

    def _edit_dialog(self, eq: Equipment, is_new: bool):
        groups = list(self.store.groups)
        has_pw = secret.AVAILABLE and not is_new and self.passwords.has(eq.id)
        dlg = EquipmentDialog(self.root, eq, is_new, groups, has_pw, self.cfg.supports_password())

        def save(new: Equipment, password):
            try:
                (self.store.add if is_new else self.store.update)(new)
            except StoreError as e:
                messagebox.showwarning(APP_NAME, str(e), parent=dlg.top)
                self.reload()
                return False
            if secret.AVAILABLE and password is not None:
                try:
                    self.passwords.set(new.id, password)
                except OSError as e:
                    messagebox.showwarning(
                        APP_NAME, f"설비는 저장했지만 비밀번호는 저장하지 못했습니다.\n({e})",
                        parent=dlg.top)
            self.reload()
            self.check_status([new])
            self.toast(f"{new.name} 설비를 {'추가' if is_new else '수정'}했습니다.")
            return True

        dlg.on_save = save

    def delete(self, eq: Equipment | None):
        if not eq or not messagebox.askyesno(
                APP_NAME, f"설비를 목록에서 삭제할까요?\n\n{eq.name}  ({eq.host})",
                icon="warning", default="no"):
            return
        try:
            self.store.delete([eq.id])
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        else:
            try:
                self.passwords.delete([eq.id])
            except OSError:
                pass
            self.toast(f"{eq.name} 설비를 삭제했습니다.")
        self.reload()

    def import_csv(self):
        path = filedialog.askopenfilename(title="CSV 가져오기",
                                          filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            added, updated, problems, passwords = self.store.import_csv(path)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self.reload()
        msg = f"설비 추가 {added}대, 수정 {updated}대"
        if passwords:
            msg += "\n" + self._remember_passwords(passwords)
        if problems:
            msg += f"\n\n건너뛴 줄 {len(problems)}개:\n" + "\n".join(problems[:10])
            if len(problems) > 10:
                msg += "\n..."
        (messagebox.showwarning if problems else messagebox.showinfo)(APP_NAME, msg)
        self.check_status()

    def _remember_passwords(self, passwords: dict) -> str:
        if not secret.AVAILABLE:
            return "비밀번호 저장은 Windows에서만 됩니다."
        try:
            self.passwords.set_many(passwords)
        except OSError as e:
            return f"비밀번호를 저장하지 못했습니다. 프로그램 폴더의 쓰기 권한을 확인하세요.\n({e})"
        text = (f"비밀번호 저장 {len(passwords)}대 - 이 PC에 암호화해 기억했으므로 "
                "다시 가져올 필요가 없습니다.\n\n"
                "가져온 파일에는 비밀번호가 그대로 적혀 있으니 안전한 곳에 두거나 지우세요.")
        if not self.cfg.supports_password():
            text += "\n\n지금 설정된 뷰어는 비밀번호 자동 입력을 지원하지 않아, 접속할 때 뷰어가 직접 묻습니다."
        return text

    def export_csv(self):
        path = filedialog.asksaveasfilename(title="CSV 내보내기", defaultextension=".csv",
                                            initialfile="설비목록.csv",
                                            filetypes=[("CSV 파일", "*.csv")])
        if not path:
            return
        try:
            export_csv(sorted(self.items, key=lambda e: (e.group, natural_key(e.name))), path)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        else:
            self.toast(f"CSV로 내보냈습니다: {path}")

    def open_settings(self):
        dlg = SettingsDialog(self.root, self.cfg)

        def save(cfg: Config):
            old = self.cfg
            self.cfg = cfg
            if not self._save_config():
                self.cfg = old
                return False
            self._open_store()
            self.status.clear()
            if self.reload():
                self.check_status()
            return True

        dlg.on_save = save

    # ------------------------------------------------------- 상태 확인 ---

    def check_status(self, items=None):
        items = list(self.items if items is None else items)
        if not items:
            return
        timeout = self.cfg.check_timeout
        for e in items:
            if self.status.get(e.id) is None:
                self.status[e.id] = "busy"
            self.pending += 1
            self.pool.submit(lambda e=e: self.results.put(
                (e.id, e.host, e.port, is_reachable(e.host, e.port, timeout))))
        self.check_btn.set_enabled(False, "확인 중…")
        self.draw()

    def _poll_results(self):
        changed = False
        by_id = {e.id: e for e in self.items}
        while True:
            try:
                eq_id, host, port, ok = self.results.get_nowait()
            except queue.Empty:
                break
            self.pending -= 1
            cur = by_id.get(eq_id)
            if cur and (cur.host, cur.port) == (host, port):
                changed = changed or self.status.get(eq_id) != ok
                self.status[eq_id] = ok
        if changed:
            self.draw()
        if self.pending <= 0 and not self.check_btn.enabled:
            self.pending = 0
            self.check_btn.set_enabled(True, "상태 확인")
        self.root.after(300, self._poll_results)


# ------------------------------------------------------------- 대화상자 ---

class _Dialog:
    def __init__(self, parent, title):
        self.on_save = None
        self.top = top = tk.Toplevel(parent)
        top.title(title)
        top.transient(parent)
        top.resizable(False, False)
        top.configure(bg=CARD)
        self.body = ttk.Frame(top, padding=22)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(1, weight=1)
        self._row = 0
        top.bind("<Escape>", lambda _e: top.destroy())

    def field(self, label, widget, hint=""):
        ttk.Label(self.body, text=label).grid(row=self._row, column=0, sticky="w", pady=6)
        widget.grid(row=self._row, column=1, sticky="ew", padx=(14, 0), pady=6)
        self._row += 1
        if hint:
            ttk.Label(self.body, text=hint, foreground=SUB).grid(
                row=self._row, column=1, sticky="w", padx=(14, 0))
            self._row += 1

    def buttons(self, ok_text="저장"):
        bar = ttk.Frame(self.body)
        bar.grid(row=self._row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="취소", command=self.top.destroy).pack(side="right")
        ttk.Button(bar, text=ok_text, style="Accent.TButton", command=self.submit).pack(side="right", padx=8)
        self.top.bind("<Return>", lambda _e: self.submit())
        self.top.update_idletasks()
        p = self.top.master
        x = p.winfo_rootx() + (p.winfo_width() - self.top.winfo_width()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.top.winfo_height()) // 3
        self.top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        try:
            self.top.grab_set()
        except tk.TclError:
            pass

    def submit(self):
        raise NotImplementedError


class EquipmentDialog(_Dialog):
    def __init__(self, parent, eq, is_new, groups, has_pw, pw_supported):
        super().__init__(parent, "설비 추가" if is_new else "설비 수정")
        self.eq, self.has_pw = eq, has_pw
        b = self.body
        self.name = tk.StringVar(value=eq.name)
        self.group = tk.StringVar(value=eq.group)
        self.host = tk.StringVar(value=eq.host)
        self.port = tk.StringVar(value=str(eq.port))
        self.os = tk.StringVar(value=eq.os)
        self.share = tk.StringVar(value=eq.share)
        self.memo = tk.StringVar(value=eq.memo)
        self.share_on = tk.BooleanVar(value=eq.share_enabled)
        self.pw = tk.StringVar()
        self.pw_clear = tk.BooleanVar(value=False)

        first = ttk.Entry(b, textvariable=self.name, width=36)
        self.field("설비명 *", first)
        self.field("그룹", ttk.Combobox(b, textvariable=self.group, values=groups),
                   "라인, 공정 등 묶어 볼 이름 (선택)")
        self.field("IP *", ttk.Entry(b, textvariable=self.host))
        self.field("VNC 포트", ttk.Entry(b, textvariable=self.port, width=10), "기본값 5900")
        os_box = ttk.Combobox(b, textvariable=self.os, values=OS_CHOICES, state="readonly")
        self.field("OS", os_box)
        self.share_entry = ttk.Entry(b, textvariable=self.share)
        self.field("공유폴더", self.share_entry,
                   "예: D$ 또는 Data  (비우면 \\\\IP 의 공유 목록 전체)")
        self.share_check = ttk.Checkbutton(b, text="이 설비에 공유폴더 버튼 보이기", variable=self.share_on,
                                           command=self._sync)
        self.field("", self.share_check)
        self.field("메모", ttk.Entry(b, textvariable=self.memo))
        if secret.AVAILABLE:
            if not pw_supported:
                hint = "지금 설정된 뷰어는 비밀번호 자동 입력을 지원하지 않습니다."
            elif has_pw:
                hint = "저장된 비밀번호가 있습니다. 바꿀 때만 입력하세요."
            else:
                hint = "입력하면 접속할 때 자동으로 넣습니다. 이 PC에만 암호화해 저장 (선택)"
            self.field("VNC 비밀번호", ttk.Entry(b, textvariable=self.pw, show="*"), hint)
            if has_pw:
                ttk.Checkbutton(b, text="저장된 비밀번호 지우기", variable=self.pw_clear).grid(
                    row=self._row, column=1, sticky="w", padx=(14, 0))
                self._row += 1
        os_box.bind("<<ComboboxSelected>>", lambda _e: self._sync())
        self._sync()
        self.buttons()
        first.focus_set()

    def _sync(self):
        is_win = self.os.get() == OS_WINDOWS
        self.share_check["state"] = "normal" if is_win else "disabled"
        self.share_entry["state"] = "normal" if is_win and self.share_on.get() else "disabled"

    def submit(self):
        is_win = self.os.get() == OS_WINDOWS
        new = Equipment.from_dict({
            "id": self.eq.id, "name": self.name.get(), "group": self.group.get(),
            "host": self.host.get(), "port": self.port.get() or "5900", "os": self.os.get(),
            "share": self.share.get() if is_win else "", "memo": self.memo.get(),
            "share_enabled": self.share_on.get()})
        password = None  # None = 건드리지 않음
        if self.pw_clear.get():
            password = ""
        elif self.pw.get():
            password = self.pw.get()
        if self.on_save and self.on_save(new, password):
            self.top.destroy()


class SettingsDialog(_Dialog):
    def __init__(self, parent, cfg: Config):
        super().__init__(parent, "설정")
        self.cfg = cfg
        b = self.body
        self.keys = list(VIEWERS)
        self.vtype = tk.StringVar(value=VIEWERS[cfg.viewer_type]["label"])
        self.vpath = tk.StringVar(value=cfg.viewer_path)
        self.extra = tk.StringVar(value=cfg.extra_args)
        self.cargs = tk.StringVar(value=cfg.custom_args)
        self.cpw = tk.StringVar(value=cfg.custom_pw_args)
        self.efile = tk.StringVar(value=cfg.equipment_file)
        self.check = tk.BooleanVar(value=cfg.check_on_start)
        self.show_share = tk.BooleanVar(value=cfg.show_share)
        self.title_name = tk.BooleanVar(value=cfg.title_with_name)
        self.quiet = tk.BooleanVar(value=cfg.quiet_connect)

        box = ttk.Combobox(b, textvariable=self.vtype, state="readonly",
                           values=[VIEWERS[k]["label"] for k in self.keys])
        self.field("VNC 뷰어 종류", box)
        row = ttk.Frame(b)
        ttk.Entry(row, textvariable=self.vpath, width=52).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="찾기", width=6, command=self._browse_viewer).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="자동", width=6, command=self._detect).pack(side="left", padx=(4, 0))
        self.field("뷰어 실행 파일", row, "예: C:\\Program Files\\TightVNC\\tvnviewer.exe")
        self.cargs_entry = ttk.Entry(b, textvariable=self.cargs)
        self.field("접속 인자", self.cargs_entry, "{host} {port} 자리에 설비 값이 들어갑니다")
        self.cpw_entry = ttk.Entry(b, textvariable=self.cpw)
        self.field("비밀번호 인자", self.cpw_entry, "{password} 사용. 비우면 비밀번호를 넘기지 않습니다")
        self.field("추가 옵션", ttk.Entry(b, textvariable=self.extra), "모든 접속에 덧붙일 옵션 (선택)")
        row2 = ttk.Frame(b)
        ttk.Entry(row2, textvariable=self.efile).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="찾기", width=6, command=self._browse_file).pack(side="left", padx=(4, 0))
        self.field("설비 목록 파일", row2,
                   "비우면 프로그램 폴더의 equipment.json. 공유 폴더 경로를 넣으면 여러 PC가 같이 씁니다")
        self.field("", ttk.Checkbutton(b, text="프로그램을 켤 때 설비 상태 확인", variable=self.check))
        self.field("", ttk.Checkbutton(b, text="VNC 창 제목 표시줄에 설비명 표시", variable=self.title_name))
        self.field("", ttk.Checkbutton(b, text="접속 도중 뜨는 상태창 숨기기", variable=self.quiet),
                   "UltraVNC 에 적용. 비밀번호 입력창은 설비 비밀번호를 저장해 두면 뜨지 않습니다")
        self.field("", ttk.Checkbutton(b, text="공유폴더 버튼 사용", variable=self.show_share),
                   "끄면 모든 설비에서 사라집니다. 설비별로 끄려면 설비를 오른쪽 클릭하세요")
        box.bind("<<ComboboxSelected>>", lambda _e: self._sync())
        self._sync()
        self.buttons()

    def _key(self) -> str:
        for k in self.keys:
            if VIEWERS[k]["label"] == self.vtype.get():
                return k
        return "custom"

    def _sync(self):
        key = self._key()
        custom = key == "custom"
        if not custom:
            self.cargs.set(VIEWERS[key]["args"])
            self.cpw.set(VIEWERS[key]["pw"])
        else:
            self.cargs.set(self.cfg.custom_args)
            self.cpw.set(self.cfg.custom_pw_args)
        for w in (self.cargs_entry, self.cpw_entry):
            w["state"] = "normal" if custom else "readonly"

    def _browse_viewer(self):
        path = filedialog.askopenfilename(parent=self.top, title="VNC 뷰어 실행 파일",
                                          filetypes=[("실행 파일", "*.exe"), ("모든 파일", "*.*")])
        if path:
            self.vpath.set(os.path.normpath(path))

    def _browse_file(self):
        path = filedialog.asksaveasfilename(parent=self.top, title="설비 목록 파일",
                                            initialfile="equipment.json", confirmoverwrite=False,
                                            filetypes=[("JSON 파일", "*.json")])
        if path:
            self.efile.set(os.path.normpath(path))

    def _detect(self):
        found = detect_viewer()
        if found:
            self.vtype.set(VIEWERS[found[0]]["label"])
            self.vpath.set(found[1])
            self._sync()
        else:
            messagebox.showinfo(APP_NAME, "설치된 VNC 뷰어를 찾지 못했습니다.\n"
                                "[찾기]를 눌러 실행 파일을 직접 지정하세요.", parent=self.top)

    def submit(self):
        key = self._key()
        path = self.vpath.get().strip().strip('"')
        if path and not os.path.isfile(path) and not messagebox.askyesno(
                APP_NAME, f"뷰어 실행 파일이 없습니다.\n{path}\n\n그래도 저장할까요?", parent=self.top):
            return
        cfg = Config(
            viewer_type=key, viewer_path=path, extra_args=self.extra.get().strip(),
            custom_args=self.cargs.get().strip() if key == "custom" else self.cfg.custom_args,
            custom_pw_args=self.cpw.get().strip() if key == "custom" else self.cfg.custom_pw_args,
            equipment_file=self.efile.get().strip().strip('"'),
            check_on_start=self.check.get(), check_timeout=self.cfg.check_timeout,
            show_share=self.show_share.get(), title_with_name=self.title_name.get(),
            quiet_connect=self.quiet.get())
        if self.on_save and self.on_save(cfg):
            self.top.destroy()


def ask_group_name(parent, title: str, initial: str, existing) -> str | None:
    """그룹 이름을 묻는다. 비었거나 이미 있는 이름이면 다시 묻는다."""
    while True:
        name = simpledialog.askstring(title, "그룹 이름", initialvalue=initial, parent=parent)
        if name is None:
            return None
        name = name.strip()
        taken = {g.casefold() for g in existing if g != initial} | {ALL_GROUPS, NO_GROUP}
        if not name:
            messagebox.showwarning(APP_NAME, "그룹 이름을 입력하세요.", parent=parent)
        elif name.casefold() in taken:
            messagebox.showwarning(APP_NAME, f"이미 있는 이름입니다: {name}", parent=parent)
        else:
            return name


class GroupDialog(_Dialog):
    """그룹 만들기/이름 변경/삭제/순서, 그룹에 넣을 설비 고르기. [저장]을 눌러야 반영된다."""

    def __init__(self, parent, groups, items):
        super().__init__(parent, "그룹 관리")
        self.groups = list(groups)
        self.items = sorted(items, key=lambda e: natural_key(e.name))
        self.assign = {e.id: e.group for e in items}
        self.current: str | None = None
        b = self.body
        list_opts = dict(exportselection=False, activestyle="none", relief="flat", bg=CARD, fg=TEXT,
                         highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                         selectbackground=ACCENT, selectforeground="#ffffff")

        left = ttk.Frame(b)
        left.grid(row=0, column=0, sticky="ns")
        ttk.Label(left, text="그룹").pack(anchor="w", pady=(0, 6))
        self.glist = tk.Listbox(left, width=22, height=16, **list_opts)
        self.glist.pack(fill="y", expand=True)
        self.glist.bind("<<ListboxSelect>>", lambda _e: self._pick())
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(8, 0))
        for text, cmd in (("추가", self._add), ("이름 변경", self._rename), ("삭제", self._delete)):
            ttk.Button(bar, text=text, command=cmd, padding=(8, 4)).pack(side="left", padx=(0, 4))
        bar2 = ttk.Frame(left)
        bar2.pack(fill="x", pady=(4, 0))
        ttk.Button(bar2, text="▲ 위로", command=lambda: self._shift(-1), padding=(8, 4)).pack(side="left", padx=(0, 4))
        ttk.Button(bar2, text="▼ 아래로", command=lambda: self._shift(1), padding=(8, 4)).pack(side="left")

        right = ttk.Frame(b)
        right.grid(row=0, column=1, sticky="nsew", padx=(18, 0))
        self.title_var = tk.StringVar()
        ttk.Label(right, textvariable=self.title_var).pack(anchor="w", pady=(0, 6))
        wrap = ttk.Frame(right)
        wrap.pack(fill="both", expand=True)
        self.elist = tk.Listbox(wrap, width=40, height=18, selectmode="multiple", **list_opts)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.elist.yview)
        self.elist.configure(yscrollcommand=sb.set)
        self.elist.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.elist.bind("<<ListboxSelect>>", lambda _e: self._toggle())
        ttk.Label(right, foreground=SUB,
                  text="클릭해서 고른 설비가 이 그룹에 들어갑니다. 다시 클릭하면 빠집니다.").pack(anchor="w", pady=(6, 0))
        self._row = 1
        self._fill_groups(0 if self.groups else None)
        self.buttons()
        self.top.unbind("<Return>")

    def _fill_groups(self, select: int | None):
        self.glist.delete(0, "end")
        for g in self.groups:
            count = sum(1 for v in self.assign.values() if v == g)
            self.glist.insert("end", f" {g}   ({count})")
        if select is not None and self.groups:
            select = max(0, min(select, len(self.groups) - 1))
            self.glist.selection_set(select)
            self.glist.see(select)
            self.current = self.groups[select]
        else:
            self.current = None
        self._fill_items()

    def _pick(self):
        sel = self.glist.curselection()
        self.current = self.groups[sel[0]] if sel else None
        self._fill_items()

    def _fill_items(self):
        top = self.elist.yview()[0]
        self.elist.delete(0, "end")
        if self.current is None:
            self.title_var.set("왼쪽에서 그룹을 고르거나 [추가]로 새 그룹을 만드세요")
            self.elist["state"] = "disabled"
            return
        self.elist["state"] = "normal"
        self.title_var.set(f"'{self.current}' 그룹에 넣을 설비")
        for i, e in enumerate(self.items):
            g = self.assign[e.id]
            other = "" if g == self.current else f"     - {g or NO_GROUP}"
            self.elist.insert("end", f" {e.name}{other}")
            if g == self.current:
                self.elist.selection_set(i)
            else:
                self.elist.itemconfigure(i, foreground=SUB)
        self.elist.yview_moveto(top)

    def _toggle(self):
        if self.current is None:
            return
        chosen = set(self.elist.curselection())
        for i, e in enumerate(self.items):
            if i in chosen:
                self.assign[e.id] = self.current
            elif self.assign[e.id] == self.current:
                self.assign[e.id] = ""
        self._fill_groups(self.groups.index(self.current))

    def _add(self):
        name = ask_group_name(self.top, "그룹 추가", "", self.groups)
        if name:
            self.groups.append(name)
            self._fill_groups(len(self.groups) - 1)

    def _rename(self):
        if self.current is None:
            return
        old = self.current
        name = ask_group_name(self.top, "그룹 이름 변경", old, self.groups)
        if name and name != old:
            i = self.groups.index(old)
            self.groups[i] = name
            for k, v in self.assign.items():
                if v == old:
                    self.assign[k] = name
            self._fill_groups(i)

    def _delete(self):
        if self.current is None:
            return
        g = self.current
        count = sum(1 for v in self.assign.values() if v == g)
        if count and not messagebox.askyesno(
                APP_NAME, f"'{g}' 그룹을 삭제할까요?\n\n설비 {count}대는 삭제되지 않고 '{NO_GROUP}'으로 옮겨집니다.",
                parent=self.top):
            return
        i = self.groups.index(g)
        self.groups.remove(g)
        for k, v in self.assign.items():
            if v == g:
                self.assign[k] = ""
        self._fill_groups(i if self.groups else None)

    def _shift(self, step: int):
        if self.current is None:
            return
        i = self.groups.index(self.current)
        j = i + step
        if 0 <= j < len(self.groups):
            self.groups[i], self.groups[j] = self.groups[j], self.groups[i]
            self._fill_groups(j)

    def submit(self):
        if self.on_save and self.on_save(self.groups, self.assign):
            self.top.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

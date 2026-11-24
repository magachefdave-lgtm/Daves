"""tkinter 화면 - 설비를 카드형 버튼으로 보여 준다."""
from __future__ import annotations

import dataclasses
import datetime
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import traceback
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import APP_NAME, APP_VERSION, secret, wintitle
from .core import (BACKUP_KEEP, CAUTION_MAX, CAUTION_PRESETS, OS_CHOICES, OS_WINDOWS, PIN_MIN,
                   VIEWERS, Config, Equipment, EquipmentStore, StoreError, app_dir, build_command,
                   resource_path,
                   check_pin, detect_viewer, equipment_file, export_csv, has_rdp, has_share,
                   CHECK_METHODS, check_method, probe, load_config, natural_key, rdp_command, read_csv, save_config,
                   share_path)
from .history import ConnectionLog
from .local import LocalState, fit_geometry, quick_ids
from .pack import PackError, make_payload, own_exe, read_pack, unseal, write_pack
from .sign import fingerprint
from .site_defaults import SHARED_EQUIPMENT_FILE, UPDATE_FOLDER, UPDATE_PUBLIC_KEY
from .update import (KEY_FILE, SigningKeyStore, UpdateError, cleanup_old, install, is_newer, publish,
                     read_latest, relaunch, verify_info)

GUIDE = "시작하기.txt"                                   # exe 안에 넣어 두는 사용법
TEMPLATES = ("설비목록_예시.csv", "비밀번호_예시.csv")    # exe 안에 넣어 두는 CSV 양식

ALL_GROUPS = "전체"
NO_GROUP = "그룹 없음"

# 색 - 두 가지 원칙을 같이 쓴다.
# 1) 산업용 모니터링 화면(ISA-101 계열): 바탕과 정상 상태는 무채색, 색은 이상과 접속에만 쓴다.
#    빨강 = 응답 없음, 주황 = 주의 표시, 파랑 = 접속 중. 색을 못 가려도 알 수 있게 모양도 다르다.
# 2) 질감: 은회색 바탕 위에 받침판(그룹)을 깔고 그 위에 흰 버튼을 띄운다. 테두리는 머리카락 굵기,
#    그림자는 옅게, 모서리는 받침판과 버튼이 같은 중심을 갖도록(받침판 16 = 여백 6 + 버튼 10).
BG = "#eceef1"          # 은회색 바탕
TRAY = "#e2e5e9"        # 그룹 받침판
TRAY_LINE = "#d5d9df"
CARD = "#ffffff"        # 대화상자, 메뉴
TILE = "#fdfdfe"        # 설비 버튼 (정상)
BORDER = "#e2e6eb"      # 버튼 테두리 (머리카락 굵기)
SHADOW = "#cfd4db"      # 버튼 아래 옅은 그림자
SHADOW_DEEP = "#b3bac4"
TEXT = "#171c22"
SUB = "#68727e"
HEAD = "#1a2028"        # 떠 있는 위쪽 바, 고른 설비의 테두리
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
CAUTION = "#c27c0e"     # 주의 표시 (PM 중 등)
CAUTION_BG = "#fdf3dd"
CAUTION_LINE = "#e9c681"
CAUTION_TEXT = "#6b4400"
IDLE = "#98a2ae"
ICON_QUIET = "#c3cad3"  # 평소의 공유폴더·원격 데스크톱 아이콘 (마우스를 올린 버튼에서만 진해진다)
NAME_X = 16             # 설비 이름이 시작하는 위치 (버튼 왼쪽에서)
STRIP_X, STRIP_W = 6, 3 # 상태 막대 (왼쪽 세로 막대) 위치·굵기
STATUS_WORD = {True: "접속 가능", False: "응답 없음", "busy": "확인 중"}
RADIUS = 10             # 설비 버튼 모서리
TRAY_PAD = 6            # 받침판 안쪽 여백
TRAY_RADIUS = RADIUS + TRAY_PAD
ICON_ZONES = ("share", "rdp")
CHECK_SHORT = {"auto": "자동", "port": "VNC 포트", "ping": "ping", "off": "하지 않음"}
UPDATE_CHECK_HOURS = 3        # 켜 둔 채로 있어도 이만큼마다 새 버전을 확인한다 (latest.json 한 번 읽기)
RELOCK_SECONDS = 30 * 60   # 편집 잠금을 푼 뒤 이만큼 편집하지 않으면 다시 잠근다
QUICK = "q"                # 맨 위 "즐겨찾기 · 최근" 줄의 버튼 key 앞에 붙인다

# 버튼의 평소 모습: (바탕, 테두리, 글자, 아이콘, 그림자, 테두리 굵기, 접속 중인지)
BLANK = (TILE, BORDER, TEXT, ICON_QUIET, SHADOW, 1, False)


def mix(a: str, b: str, t: float) -> str:
    """색 a 에서 b 로 t(0~1)만큼 섞는다."""
    t = max(0.0, min(1.0, t))
    pa, pb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def ease(t: float) -> float:
    """빨리 출발해 천천히 멎는 곡선 - 물건이 무게를 갖고 자리에 앉는 느낌."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 4


# 설비 버튼 크기: 화면에 다 들어오도록 TILE_W_WIDE 에서 TILE_W_NARROW 까지 줄이고,
# 그래도 넘치면 높이를 TILE_H 에서 TILE_H_COMPACT 로 낮춘다.
TILE_W_WIDE, TILE_W_NARROW, TILE_H, TILE_H_COMPACT, GAP, PAD, GROUP_GAP = 176, 118, 36, 30, 6, 16, 8
ICON_STEP = 26          # 버튼 안 아이콘(공유폴더·원격 데스크톱) 하나가 차지하는 폭


HANGUL_PIN = "PIN에 한글이 들어갔습니다. 한/영 키를 눌러 영문 상태에서 다시 입력하세요."
HANGUL_PHRASE = "배포 암호에 한글이 들어갔습니다. 한/영 키를 눌러 영문 상태에서 다시 입력하세요."
HANGUL_KEY_PHRASE = "암호에 한글이 들어갔습니다. 한/영 키를 눌러 영문 상태에서 다시 입력하세요."


def wrong_pin_text(pin: str) -> str:
    if not pin.isascii():
        return HANGUL_PIN
    return ("PIN이 맞지 않습니다.\n\n"
            "PIN을 잊었으면 [더보기 > 사용법 보기]의 '11. 편집 잠금'을 보세요. (--reset-pin)")


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


def island(parent, height: int, top: int, bottom: int) -> tuple[tk.Canvas, tk.Frame]:
    """창 가장자리에 붙이지 않고 둥근 알약 모양으로 띄운 어두운 바. (바, 내용을 넣을 틀)"""
    bar = tk.Canvas(parent, bg=BG, height=height, highlightthickness=0)
    row = tk.Frame(bar, bg=HEAD)
    win = bar.create_window(PAD + 20, (top + bottom) // 2, anchor="w", window=row)
    radius = (bottom - top) // 2

    def lay(event):
        bar.delete("island")
        for grow, color in ((3, mix(BG, "#000000", 0.03)), (2, mix(BG, "#000000", 0.06)),
                            (1, mix(BG, "#000000", 0.10))):   # 옅게 퍼지는 그림자
            round_rect(bar, PAD - grow, top - grow + 3, event.width - PAD + grow, bottom + grow + 3,
                       radius + grow, fill=color, outline="", tags="island")
        round_rect(bar, PAD, top, event.width - PAD, bottom, radius, fill=HEAD, outline="", tags="island")
        bar.tag_lower("island")
        bar.itemconfigure(win, width=event.width - 2 * PAD - 32)

    bar.bind("<Configure>", lay)
    return bar, row


class PillButton(tk.Canvas):
    """알약 모양 버튼. 마우스를 올리면 색이 부드럽게 바뀌고, 누르면 살짝 내려앉는다."""

    def __init__(self, parent, text, command, font, colors, bg):
        super().__init__(parent, height=30, highlightthickness=0, bg=bg, cursor="hand2")
        self.font, self.colors, self.command, self.enabled = font, colors, command, True
        self.shift = False
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
            self.shift = bool(event.state & 0x1)   # Shift 를 누른 채 눌렀는지 ([더보기]의 관리자 메뉴)
            self.command()

    def set_enabled(self, flag: bool, text: str | None = None):
        if self.job:
            self.after_cancel(self.job)
            self.job = None
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
        # 설비 버튼은 key 로 구분한다: 설비 id, 맨 위 "즐겨찾기 · 최근" 줄의 버튼은 "q" + id.
        # 같은 설비가 두 번 보여도 마우스를 올린 버튼만 반응하게 하려는 것이다.
        self.hover = (None, None)   # (버튼 key, 영역)
        self.pressed = (None, None)
        self.inst: dict[str, str] = {}     # 버튼 key -> 설비 id
        self.order: list[str] = []         # 그룹 순서대로 그린 설비 id (Shift+클릭 범위 선택)
        # 떠 있는 뷰어·원격 데스크톱: {id, name, host, kind, proc, started, connected}
        # connected=False 는 "접속 시도 중"
        self.sessions: list[dict] = []
        self.boxes: dict[str, tuple] = {}  # 접속 시도 중인 버튼의 위치 (움직이는 막대용)
        self.base: dict[str, tuple] = {}   # 버튼의 평소 모습 (마우스가 떠날 때 되돌린다)
        self.selecting = False             # 여러 대 선택 중
        self.selected: set[str] = set()
        self.anchor: str | None = None     # Shift+클릭 범위의 시작
        self.pool = ThreadPoolExecutor(max_workers=16)
        self.log_pool = ThreadPoolExecutor(max_workers=1)  # 접속 이력은 순서대로, 화면을 막지 않게
        # 업데이트 확인·받기·올리기처럼 공유 폴더를 오래 붙잡는 일 (상태 확인 일꾼과 따로 둔다)
        self.bg_pool = ThreadPoolExecutor(max_workers=2)
        self.jobs: queue.Queue = queue.Queue()   # 일꾼 스레드가 끝낸 일: (화면 스레드에서 부를 함수, 결과, 오류)
        self.results: queue.Queue = queue.Queue()
        self.pending = 0
        self.inflight: set[str] = set()   # 지금 확인 중인 설비 id
        self.last_check = time.monotonic()  # 마지막 상태 확인이 끝난 때
        self.last_update_check = time.monotonic()
        self.rdp_warned = False
        self.checked_at = ""                # 화면에 보일 그 시각
        self.newly_down: list[str] = []     # 이번 확인에서 응답 없음으로 바뀐 설비
        self.unlocked_at = 0.0              # 편집 잠금을 푼(또는 마지막으로 편집한) 때. 0 = 잠김
        self.seen_mtime = 0.0
        self.scrollable = False
        self.only_down = False   # 응답 없는 설비만 보기
        self.gen = 0             # 화면을 다시 그릴 때마다 1 증가
        self.sunk = None         # 눌려서 내려앉은 설비 버튼
        self.reveal = 0.0        # 처음 나타나는 정도 (1 이상 = 다 나타남)
        self.normal_geometry = ""  # 최대화하기 전의 창 크기·위치
        self.was_zoomed = False    # 최소화하기 전에 최대화돼 있었는지
        self.th = TILE_H           # 지금 쓰는 버튼 높이
        self._draw_job = self._toast_job = None
        self.store = EquipmentStore("")
        self.log = ConnectionLog(app_dir())
        self.local = LocalState(os.path.join(app_dir(), "ui_state.json"))

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
            self.cfg.equipment_file = SHARED_EQUIPMENT_FILE
            self.cfg.update_folder = UPDATE_FOLDER
            self._save_config()
        self.passwords = secret.PasswordStore(os.path.join(app_dir(), "passwords.json"))
        self._open_store()
        pack_ok = self._maybe_apply_pack(first_run)   # 배포본이면 넣어 둔 설정을 가져온다
        cleanup_old(own_exe())                         # 지난 업데이트에서 남은 예전 exe
        self.update_info: dict | None = None           # 서버에 있는 새 버전 정보 (서명 확인을 거친 것)
        self.updating = False                          # 새 버전을 받는 중 / 올리는 중
        self.update_checking = False                   # latest.json 을 읽는 중 (한 번에 하나만)
        self.update_manual = False                     # 그 확인을 사용자가 직접 눌렀는지
        self.update_rejected = ""                      # 서명이 맞지 않아 받지 않은 버전 (같은 경고를 되풀이하지 않게)
        self.check_update(manual=False)
        self.group = self.local.group or ALL_GROUPS
        self._restore_window()
        root.protocol("WM_DELETE_WINDOW", self.close)
        # Windows 로그오프·종료·업데이트 재시작 때는 WM_DELETE_WINDOW 없이 이것만 온다
        root.protocol("WM_SAVE_YOURSELF", self._save_window_state)
        root.bind("<Configure>", self._on_root_configure, add="+")
        self._show_hint()
        self.reload()
        self.root.after(60, self._reveal)
        if self.cfg.check_on_start:
            self.check_status()
        self.root.after(300, self._poll_results)
        self.root.after(5000, self._watch_file)
        self.root.after(1000, self._tick_sessions)
        self.root.after(15000, self._tick_clock)
        if first_run and not pack_ok:   # 처음 켠 사람: 뷰어와 설비 목록 위치를 확인하게 한다
            self.root.after(900, lambda: self.open_settings(first=True))

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
        quiet = (HEAD_FIELD, HEAD_HOVER, HEAD_TEXT)
        # 위쪽 바
        head, row1 = island(root, 64, 11, 55)
        head.pack(fill="x")
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
        # 새 버전이 있을 때만 나타난다 (파랑 - 누르면 업데이트)
        self.update_btn = PillButton(row1, "", self.start_update, self.f_group,
                                     (ACCENT, ACCENT_DARK, "#ffffff"), HEAD)

        # 그룹 줄. 오른쪽 끝에는 지금 접속해 있는 설비가 나온다.
        self.chips = tk.Canvas(root, bg=BG, height=28, highlightthickness=0)
        self.chips.pack(fill="x", padx=PAD, pady=(2, 6))
        self.chips.bind("<Configure>", lambda _e: self._draw_chips())
        self.chips.bind("<Button-1>", self._on_chip)

        foot = tk.Frame(root, bg=BG)
        foot.pack(side="bottom", fill="x")
        # 범례는 이상 상태만 - 정상은 아무 표시 없는 이름판이다
        for mark, color, word in (("▮", ACCENT, "접속 중"), ("▲", CAUTION, "주의"), ("■", ALERT, "응답 없음")):
            tk.Label(foot, text=word, bg=BG, fg=SUB, font=self.f_small).pack(side="right", padx=(0, 14))
            tk.Label(foot, text=mark, bg=BG, fg=color, font=self.f_small).pack(side="right", padx=(0, 3))
        self.foot_var = tk.StringVar()
        tk.Label(foot, textvariable=self.foot_var, bg=BG, fg=SUB, font=self.f_small,
                 anchor="w").pack(side="left", fill="x", expand=True, padx=PAD + 4, pady=(2, 8))

        # 여러 대 선택 중에만 보이는 바 (아래쪽, 위쪽 바와 같은 모양)
        self.selbar, selrow = island(root, 52, 5, 45)
        self.sel_label = tk.Label(selrow, font=self.f_group, bg=HEAD, fg=HEAD_TEXT)
        self.sel_label.pack(side="left")
        self.sel_row = selrow
        self.sel_compact = False
        end_btn = PillButton(selrow, "끝내기  Esc", self.end_selection, self.f_body, quiet, HEAD)
        end_btn.pack(side="right")
        all_btn = PillButton(selrow, "모두 고르기", self.select_all_visible, self.f_body, quiet, HEAD)
        all_btn.pack(side="right", padx=6)
        move_btn = PillButton(selrow, "그룹  ▾", None, self.f_body, quiet, HEAD)
        caution_btn = PillButton(selrow, "주의  ▾", None, self.f_body, quiet, HEAD)
        check_btn = PillButton(selrow, "상태 확인  ▾", None, self.f_body, quiet, HEAD)
        check_btn.command = lambda: self._menu_below(check_btn, self._fill_check_menu, self.sub_check)
        move_btn.command = lambda: self._menu_below(move_btn, self._fill_move_menu, self.sub_move)
        caution_btn.command = lambda: self._menu_below(caution_btn, self._fill_caution_menu,
                                                       self.sub_caution)
        self.sel_buttons = [
            PillButton(selrow, "삭제", lambda: self.delete(sorted(self.selected)), self.f_body,
                       (ALERT, ALERT_DARK, "#ffffff"), HEAD),
            PillButton(selrow, "★ 즐겨찾기", self.favorite_selected, self.f_body, quiet, HEAD),
            check_btn, caution_btn, move_btn]
        for b in self.sel_buttons:
            b.pack(side="right", padx=(0, 6))
        # 창이 좁으면 짧은 이름으로 (버튼, 넓을 때, 좁을 때)
        self.sel_names = [(end_btn, "끝내기  Esc", "끝내기"), (all_btn, "모두 고르기", "모두"),
                          (self.sel_buttons[1], "★ 즐겨찾기", "★"), (check_btn, "상태 확인  ▾", "확인  ▾")]
        selrow.bind("<Configure>", lambda _e: self._fit_selbar())
        # 설명은 맨 나중에 넣어, 창이 좁으면 버튼이 아니라 설명이 줄어들게 한다
        tk.Label(selrow, text="클릭 = 고르기·빼기    Shift+클릭 = 범위", font=self.f_small, anchor="w",
                 bg=HEAD, fg=HEAD_SUB).pack(side="left", padx=(14, 0), fill="x", expand=True)

        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.canvas = c = tk.Canvas(self.body, bg=BG, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self.body, orient="vertical", command=c.yview)
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
        c.configure(yscrollincrement=TILE_H + GAP)   # draw() 가 버튼 높이에 맞춰 다시 정한다

        root.bind("<F5>", lambda _e: (self.reload(), self.check_status()))
        root.bind("<Map>", self._on_map, add="+")
        root.bind("<Control-f>", lambda _e: self.search.focus_set())
        root.bind("<Escape>", lambda _e: self.selecting and self.end_selection())
        root.bind("<Delete>", self._on_delete_key)
        self.search.bind("<Escape>", self._on_search_escape)
        self.search.bind("<Delete>", self._on_search_delete)
        self.search.bind("<Return>", self._on_enter)

        self.menu_opts = dict(tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                              activeforeground="#ffffff", relief="flat", bd=1)
        self.menu = tk.Menu(root, **self.menu_opts)
        self.sub_move = tk.Menu(self.menu, **self.menu_opts)
        self.sub_caution = tk.Menu(self.menu, **self.menu_opts)
        self.sub_buttons = tk.Menu(self.menu, **self.menu_opts)
        self.sub_check = tk.Menu(self.menu, **self.menu_opts)
        self.more_menu = tk.Menu(root, **self.menu_opts)

    def _show_more(self):
        m = self.more_menu
        m.delete(0, "end")
        m.add_command(label="그룹 관리", command=self.open_groups)
        m.add_command(label="여러 대 선택하기", command=self.start_selection)
        m.add_separator()
        m.add_command(label="CSV 가져오기 (설비 · 비밀번호)", command=self.import_csv)
        m.add_command(label="CSV 내보내기 (설비 · 비밀번호)", command=self.export_csv)
        m.add_separator()
        m.add_command(label="접속 이력 보기", command=self.open_history)
        m.add_command(label="백업 폴더 열기", command=self.open_backups)
        m.add_separator()
        if self.store.lock:
            if self.unlocked_at:
                m.add_command(label="편집 다시 잠그기", command=self.relock)
            else:
                m.add_command(label="편집 잠금 풀기", command=self._can_edit)
        m.add_command(label="설정", command=self.open_settings)
        m.add_command(label="업데이트 확인", command=lambda: self.check_update(manual=True))
        m.add_command(label="사용법 보기", command=self.open_guide)
        m.add_command(label="CSV 양식 저장…", command=self.save_templates)
        m.add_command(label="설정 폴더 열기", command=lambda: self._open_path(app_dir()))
        # 관리자 메뉴: 배포본으로 설치한 PC(부서원)에는 보이지 않는다. 관리자는 Shift+[더보기]로 연다.
        admin = self.is_admin()
        if admin or self.more_btn.shift:
            m.add_separator()
            m.add_command(label="관리자", state="disabled")
            m.add_command(label="배포본 만들기…", command=self.make_pack)
            m.add_command(label="업데이트 올리기…", command=self.publish_update)
            m.add_command(label="업데이트 서명 키…", command=self.open_signing_key)
            m.add_command(label="편집 잠금 설정…", command=self.open_lock)
            m.add_command(label="관리자 메뉴 숨기기 (부서원 PC처럼)" if admin else
                          "이 PC에서 관리자 메뉴 늘 보이기", command=lambda: self.set_admin(not admin))
        self.more_btn.shift = False
        b = self.more_btn
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 2)

    def _menu_below(self, widget, fill, menu):
        """선택 바의 버튼 아래에 메뉴를 펼친다."""
        if not self.selected:
            return
        fill(menu, sorted(self.selected))
        menu.update_idletasks()   # 한 번도 펼치지 않은 메뉴는 이걸 해야 높이를 안다
        menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() - 4 - menu.winfo_reqheight())

    def _wheel(self, step: int):
        if self.scrollable:
            self.canvas.yview_scroll(step, "units")

    def _show_hint(self):
        parts = ["클릭 = VNC 접속"]
        if self.cfg.show_share:
            parts.append("폴더 = 공유폴더")
        if self.cfg.show_rdp:
            parts.append("모니터 = 원격 데스크톱")
        parts += ["Ctrl+클릭 = 여러 대 선택", "오른쪽 클릭 = 메뉴"]
        self.hint = "   ·   ".join(parts)
        self._foot()

    # ------------------------------------------------------ 창 기억 ---

    def _is_zoomed(self) -> bool:
        try:
            if self.root.state() == "zoomed":            # Windows
                return True
        except tk.TclError:
            pass
        try:
            return bool(int(self.root.attributes("-zoomed")))   # Linux
        except (tk.TclError, ValueError):
            return False

    def _set_zoomed(self):
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def _restore_window(self):
        screen = wintitle.virtual_screen() or (
            0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        geometry = fit_geometry(self.local.geometry, screen, (760, 420))
        if geometry:
            self.root.geometry(geometry)
            self.normal_geometry = geometry
        if self.local.zoomed:
            self._set_zoomed()
        self.was_zoomed = self.local.zoomed

    def _on_root_configure(self, event):
        # 최대화하지 않은 때의 크기·위치만 기억한다 - 최대화를 풀었을 때 돌아갈 자리.
        # 최소화된 동안은 아무것도 바꾸지 않는다 (작업 표시줄에서 닫아도 최대화 상태를 지킨다).
        if event.widget is not self.root or self.root.state() == "iconic":
            return
        self.was_zoomed = self._is_zoomed()
        if self.root.state() == "normal" and not self.was_zoomed:
            self.normal_geometry = self.root.geometry()

    def _save_window_state(self):
        self.local.refresh_lists()   # 같은 PC의 다른 창에서 바꾼 즐겨찾기를 지우지 않게
        iconic = self.root.state() == "iconic"
        self.local.zoomed = self.was_zoomed if iconic else self._is_zoomed()
        self.local.geometry = self.normal_geometry or self.root.geometry()
        self.local.group = "" if self.group == ALL_GROUPS else self.group
        self.local.save()

    def close(self):
        self._save_window_state()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.bg_pool.shutdown(wait=False, cancel_futures=True)
        self.log_pool.shutdown(wait=True)   # 남은 접속 이력은 마저 쓴다
        for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):   # 예약해 둔 주기 작업을 거둔다
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self.root.destroy()

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
        return list(dict.fromkeys(s["name"] + (" (RDP)" if s["kind"] == "RDP" else "")
                                  for s in self.sessions if s["connected"] == connected))

    @staticmethod
    def _join(names: list[str]) -> str:
        return ",  ".join(names[:3]) + (f"  외 {len(names) - 3}대" if len(names) > 3 else "")

    def _draw_chips(self):
        c = self.chips
        c.delete("all")
        width = max(c.winfo_width(), 200)
        # 오른쪽 끝: 접속 표시. 자리 폭을 접속 상태와 상관없이 고정해 둔다 - 접속할 때마다
        # 그룹 줄이 두 줄로 넘쳤다 돌아오면 아래 설비 버튼 크기까지 흔들리기 때문이다.
        slot = min(380, int(width * 0.4))
        trying, active = self._session_names(False), self._session_names(True)
        if active or trying:
            if active:
                head, names, tail = "접속 중   ", active, (f"   ·   시도 중 {len(trying)}" if trying else "")
                fill, line, ink = ACCENT, ACCENT, "#ffffff"
            else:
                head, names, tail = "접속 시도 중…   ", trying, ""
                fill, line, ink = TRYING_BG, TRYING_LINE, ACCENT_DARK
            # 이름만 줄이고, 몇 대인지("외 N대", "시도 중 N")는 늘 보이게
            count = f"  외 {len(names) - 1}대" if len(names) > 1 else ""
            room_names = slot - 44 - self.f_group.measure(head + tail + count)
            if self.f_group.measure(self._join(names)) <= room_names + self.f_group.measure(count):
                text = head + self._join(names) + tail
            else:
                text = head + self._fit(names[0], self.f_group, max(room_names, 30)) + count + tail
            w = self.f_group.measure(text) + 44
            round_rect(c, width - w, 0, width - 1, 25, 13, fill=fill, outline=line)
            c.create_rectangle(width - w + 14, 7, width - w + 18, 19, fill=ink, outline="")
            c.create_text(width - w + 28, 13, anchor="w", text=text, font=self.f_group, fill=ink)
        else:
            c.create_text(width - 4, 13, anchor="e", text="접속 중인 설비 없음", font=self.f_small, fill=SUB)
        room = width - slot - 16
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
                self._save_window_state()
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
            hay = " ".join((e.name, e.group, e.host, e.os, e.memo, e.caution)).casefold()
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

    def _share_on(self, e: Equipment) -> bool:
        return self.cfg.show_share and has_share(e)

    def _rdp_on(self, e: Equipment) -> bool:
        return self.cfg.show_rdp and has_rdp(e)

    def _need(self, e: Equipment) -> int:
        """설비명이 잘리지 않으려면 필요한 버튼 폭."""
        icons = self._share_on(e) + self._rdp_on(e)
        marks = (18 if e.caution else 0) + (18 if self.status.get(e.id) is False else 0)
        return self.f_name.measure(e.name) + NAME_X + 8 + ICON_STEP * icons + marks

    def _summary(self):
        parts = [f"전체 {len(self.items)}대"]
        if self.checked_at:
            parts.append(f"{self.checked_at} 확인")
        if self.store.read_only:
            parts.append("읽기 전용")
        elif self.store.lock and self.unlocked_at:
            parts.append("편집 잠금 풀림")
        self.summary["text"] = "   ·   ".join(parts)

    def draw(self):
        self._draw_job = None
        c = self.canvas
        c.delete("all")
        self.gen += 1                 # 진행 중이던 색 전환은 여기서 끝난다
        self.hover = (None, None)
        self.base, self.boxes, self.inst = {}, {}, {}
        self.order = []
        self.sunk = None
        width, height = max(c.winfo_width(), 300), max(c.winfo_height(), 100)
        shown = self.visible()
        down = sum(1 for e in self.items if self.status.get(e.id) is False)
        self._summary()
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

        by_group: dict[str, list[Equipment]] = {}
        for e in shown:
            by_group.setdefault(e.group, []).append(e)
        names = [g for g in self.store.groups if g in by_group] + ([""] if "" in by_group else [])
        self.order = [e.id for g in names for e in by_group[g]]
        # 맨 위 "즐겨찾기" 줄은 전체를 볼 때만 (검색·그룹·응답 없음 필터 중에는 숨긴다)
        by_id = {e.id: e for e in self.items}
        quick_on = (self.group == ALL_GROUPS and not self.only_down
                    and not self.search_var.get().strip())
        fav_ids = quick_ids(self.local.favorites, by_id) if quick_on else []
        has_quick = bool(fav_ids)
        labels = has_quick or bool(names and names[0])
        # 그룹 이름은 받침판 왼쪽 칸에 둔다 - 제목 줄을 따로 쓰지 않아 세로 공간을 아낀다.
        label_w = 0
        if labels:
            titles = [g or NO_GROUP for g in names] + (["★ 즐겨찾기"] if has_quick else [])
            subs = [f"{len(by_group[g])}대" for g in names] + ([f"{len(fav_ids)}대"] if has_quick else [])
            label_w = min(140, max(max(self.f_group.measure(t) for t in titles),
                                   max(self.f_small.measure(t) for t in subs)) + 26)
        avail = width - 2 * PAD - 2 * TRAY_PAD - label_w
        cols_for = lambda tile_w: max(1, int((avail + GAP) // (tile_w + GAP)))
        top = 2

        def total(cols, th=TILE_H):
            counts = [len(by_group[g]) for g in names] + ([len(fav_ids)] if has_quick else [])
            rows = [-(-n // cols) for n in counts]
            return (sum(r * (th + GAP) - GAP + 2 * TRAY_PAD for r in rows)
                    + GROUP_GAP * (len(rows) - 1) + top + 6)

        # 넓은 버튼부터 시작해, 스크롤 없이 들어올 때까지 한 줄의 버튼 수를 늘린다.
        # 설비명 대부분(90%)이 잘리지 않는 폭까지만 줄인다. 유난히 긴 이름 몇 개는 … 로 줄인다.
        # 가장 좁혀도 넘치면 버튼 높이를 낮춘다 (그래도 넘치면 그때 스크롤).
        widths = sorted(self._need(e) for e in shown)
        narrow = min(TILE_W_WIDE, max(TILE_W_NARROW, widths[int((len(widths) - 1) * 0.9)]))
        cols, most = cols_for(TILE_W_WIDE), cols_for(narrow)
        while cols < most and total(cols) > height:
            cols += 1
        self.th = th = TILE_H if total(cols) <= height else TILE_H_COMPACT
        c.configure(yscrollincrement=th + GAP)
        tile_w = (avail - GAP * (cols - 1)) / cols

        trays = []   # (버튼 key 앞머리, 제목, 둘째 줄, 설비)
        if has_quick:
            trays.append((QUICK, "★ 즐겨찾기", f"{len(fav_ids)}대", [by_id[i] for i in fav_ids]))
        trays += [("", g or NO_GROUP, f"{len(by_group[g])}대", by_group[g]) for g in names]

        x0 = PAD + TRAY_PAD + label_w
        y = top
        for n, (prefix, title, sub, its) in enumerate(trays):
            rows = -(-len(its) // cols)
            tray_h = rows * (th + GAP) - GAP + 2 * TRAY_PAD
            # 처음 켤 때: 받침판이 위에서부터 차례로 떠오르며 나타난다.
            a = ease((self.reveal - 0.09 * min(n, 6)) / 0.45)
            lift = (1 - a) * 10
            round_rect(c, PAD, y + lift, width - PAD, y + tray_h + lift, TRAY_RADIUS,
                       fill=mix(BG, TRAY, a), outline=mix(BG, TRAY_LINE, a))
            if labels:
                ty = y + lift + TRAY_PAD + th / 2
                two = bool(sub) and (rows > 1 or prefix == QUICK)
                c.create_text(PAD + 16, ty - (7 if two else 0), anchor="w", font=self.f_group,
                              fill=mix(TRAY, TEXT, a), text=self._fit(title, self.f_group, label_w - 22))
                if two:
                    c.create_text(PAD + 16, ty + 11, anchor="w", font=self.f_small, fill=mix(TRAY, SUB, a),
                                  text=self._fit(sub, self.f_small, label_w - 22))
            for i, e in enumerate(its):
                self._tile(e, x0 + (i % cols) * (tile_w + GAP),
                           y + lift + TRAY_PAD + (i // cols) * (th + GAP), tile_w, a, prefix + e.id)
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

    def _tile(self, e: Equipment, x: float, y: float, w: float, a: float, key: str):
        c = self.canvas
        self.inst[key] = e.id
        st = self.status.get(e.id)
        body = (f"id:{e.id}", f"inst:{key}", "zone:body")
        th = self.th
        mid = y + th / 2
        mine = [s for s in self.sessions if s["id"] == e.id]
        active = any(s["connected"] for s in mine)
        trying = bool(mine) and not active
        # (바탕, 테두리, 글자, 아이콘, 그림자) - 정상은 무채색, 색은 이상(빨강·주황)과 접속(파랑)에만
        if trying:
            look = (TRYING_BG, TRYING_LINE, ACCENT_DARK, ACCENT, mix(SHADOW, ACCENT, 0.2))
        elif active:
            look = (ACCENT, ACCENT_DARK, "#ffffff", "#ffffff", mix(SHADOW, ACCENT, 0.35))
        elif st is False:
            look = (ALERT_BG, ALERT_LINE, ALERT_TEXT, ALERT_TEXT, mix(SHADOW, ALERT, 0.15))
        elif e.caution:
            look = (CAUTION_BG, CAUTION_LINE, CAUTION_TEXT, CAUTION_TEXT, mix(SHADOW, CAUTION, 0.15))
        else:   # 정상·미확인: 아무 표시 없는 이름판
            look = (TILE, BORDER, TEXT, ICON_QUIET, SHADOW)
        if a < 1:   # 나타나는 중: 받침판 색에서 제 색으로
            look = tuple(mix(TRAY, col, a) for col in look)
        fill, outline, ink, icon, shadow = look
        line_w = 1
        if e.id in self.selected:   # 고른 설비: 진한 테두리 (접속 중인 파란 버튼은 흰 테두리)
            outline, line_w = ("#ffffff" if active else HEAD), 2
            if not active:
                fill = mix(fill, HEAD, 0.07)
        self.base[key] = (fill, outline, ink, icon, shadow, line_w, active)
        # 그림자는 버튼과 따로 둔다 - 버튼만 뜨고 가라앉아 깊이가 생긴다.
        round_rect(c, x + 1, y + 2, x + w - 1, y + th + 2, RADIUS, fill=shadow, outline="",
                   tags=(f"shadow:{key}",))
        round_rect(c, x, y, x + w, y + th, RADIUS, fill=fill, outline=outline, width=line_w,
                   tags=body + (f"card:{key}",))
        # 상태 표시: 이상할 때만 왼쪽에 세로 막대 하나. 정상은 아무것도 그리지 않는다.
        strip = None
        if trying:   # 접속 시도 중: 파란 막대 + 버튼 아래쪽을 막대가 오간다 (_sweep 이 움직인다)
            strip = ACCENT
            self.boxes[key] = (x, y, w)
            c.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="", tags=body + (f"sweep:{key}",))
            self._place_sweep(key)
        elif active:
            strip = "#ffffff"
        elif st is False:
            strip = ALERT
        elif e.caution:
            strip = CAUTION
        if strip:
            sy = 8 if th >= 34 else 7
            round_rect(c, x + STRIP_X, y + sy, x + STRIP_X + STRIP_W, y + th - sy, 1,
                       fill=mix(fill, strip, a), outline="", tags=body)
        right = x + w - 6
        for kind, on in (("share", self._share_on(e)), ("rdp", self._rdp_on(e))):
            if on:   # 평소엔 옅은 선 아이콘만, 마우스를 올린 버튼에서 진해지고 아이콘 위에서는 동그란 버튼
                self._icon(kind, key, e.id, right - ICON_STEP / 2, mid, fill, icon)
                right -= ICON_STEP
        # 모양 표시 (색을 못 가려도 알 수 있게): 응답 없음 ■, 주의 ▲
        if st is False and not active and not trying:
            cx = right - 7
            c.create_rectangle(cx - 4, mid - 4, cx + 4, mid + 4, fill=mix(fill, ALERT, a), outline="", tags=body)
            right -= 18
        if e.caution:
            cx = right - 7
            c.create_polygon(cx, mid - 6, cx + 7, mid + 6, cx - 7, mid + 6, fill=mix(fill, CAUTION, a),
                             outline="#ffffff" if active else "", tags=body)
            right -= 18
        c.create_text(x + NAME_X, mid, anchor="w", font=self.f_name, tags=body, fill=ink,
                      text=self._fit(e.name, self.f_name, right - (x + NAME_X)))

    def _icon(self, kind: str, key: str, eq_id: str, cx: float, mid: float, fill: str, icon: str):
        """공유폴더(폴더)·원격 데스크톱(모니터) 아이콘. 선 모양은 ico:, 채운 모양은 icf: 태그."""
        c = self.canvas
        zone = (f"id:{eq_id}", f"inst:{key}", f"zone:{kind}")
        r = min(12, self.th / 2 - 3)
        c.create_oval(cx - r, mid - r, cx + r, mid + r, fill=fill, outline="",
                      tags=zone + (f"btn:{key}:{kind}",))
        line, solid = zone + (f"ico:{key}:{kind}",), zone + (f"icf:{key}:{kind}",)
        if kind == "share":
            c.create_polygon(cx - 6, mid - 5, cx - 2, mid - 5, cx, mid - 3, cx + 6, mid - 3,
                             cx + 6, mid + 5, cx - 6, mid + 5, fill=fill, outline=icon, tags=line)
        else:
            c.create_rectangle(cx - 7, mid - 6, cx + 7, mid + 3, fill=fill, outline=icon, tags=line)
            c.create_rectangle(cx - 1, mid + 3, cx + 1, mid + 6, fill=icon, outline="", tags=solid)
            c.create_rectangle(cx - 4, mid + 6, cx + 4, mid + 7, fill=icon, outline="", tags=solid)

    # ------------------------------------------------- 마우스, 키보드 ---

    def _target(self) -> tuple[str | None, str | None, str | None]:
        key = eq_id = zone = None
        for tag in self.canvas.gettags("current"):
            if tag.startswith("inst:"):
                key = tag[5:]
            elif tag.startswith("id:"):
                eq_id = tag[3:]
            elif tag.startswith("zone:"):
                zone = tag[5:]
        return key, eq_id, zone

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

    def _paint_icons(self, key: str, zone: str | None, hovered: bool):
        fill, _outline, _ink, icon, _shadow, _w, active = self.base.get(key, BLANK)
        c = self.canvas
        for kind in ICON_ZONES:
            if hovered and zone == kind:
                bg, fg = ("#ffffff", ACCENT) if active else (HEAD, "#ffffff")
            elif hovered:
                bg, fg = fill, ("#ffffff" if active else TEXT)
            else:
                bg, fg = fill, icon
            c.itemconfigure(f"btn:{key}:{kind}", fill=bg)
            c.itemconfigure(f"ico:{key}:{kind}", fill=bg, outline=fg)
            c.itemconfigure(f"icf:{key}:{kind}", fill=fg)

    def _set_hover(self, new):
        old = self.hover
        if new == old:
            return
        c = self.canvas
        if old[0] and old[0] != new[0]:   # 버튼을 떠날 때: 제자리로 내려앉는다
            _fill, outline, _ink, _icon, shadow, _w, active = self.base.get(old[0], BLANK)
            self._settle(None)
            c.move(f"inst:{old[0]}", 0, 1)
            self._fade(f"card:{old[0]}", "outline", "#ffffff" if active else HEAD, outline)
            self._fade(f"shadow:{old[0]}", "fill", SHADOW_DEEP, shadow)
            self._paint_icons(old[0], None, False)
        if new[0]:
            _fill, outline, _ink, _icon, shadow, _w, active = self.base.get(new[0], BLANK)
            if old[0] != new[0]:   # 버튼에 올라갈 때: 1px 떠오르고 그림자가 짙어진다
                c.move(f"inst:{new[0]}", 0, -1)
                self._fade(f"card:{new[0]}", "outline", outline, "#ffffff" if active else HEAD)
                self._fade(f"shadow:{new[0]}", "fill", shadow, SHADOW_DEEP)
            # 상태 색은 그대로 둔다 - 마우스를 올려도 상태가 가려지지 않는다.
            self._paint_icons(new[0], new[1], True)
        c.configure(cursor="hand2" if new[0] else "")
        self.hover = new
        self._foot()

    def _on_press(self, event):
        """누르면 버튼이 그림자 위로 내려앉는다."""
        self._on_motion(event)   # 화면을 다시 그린 뒤 마우스를 움직이지 않고 눌러도 알아듣게
        self.pressed = self.hover
        if self.hover[0] and not self.sunk:
            self.canvas.move(f"inst:{self.hover[0]}", 0, 2)
            self.sunk = self.hover[0]

    def _settle(self, _event):
        if self.sunk:
            self.canvas.move(f"inst:{self.sunk}", 0, -2)
            self.sunk = None

    def _foot(self):
        """아래 한 줄: 방금 한 일 > 마우스가 올라간 설비 정보 > 사용법."""
        if self._toast_job:
            return
        eq = self._by_id(self.inst.get(self.hover[0]))
        if not eq:
            self.foot_var.set(getattr(self, "hint", ""))
        elif self.hover[1] == "share":
            self.foot_var.set(f"{eq.name}  공유폴더 열기:  {share_path(eq)}")
        elif self.hover[1] == "rdp":
            self.foot_var.set(f"{eq.name}  원격 데스크톱 접속:  {eq.host}")
        else:
            st = self.status.get(eq.id)
            mine = [s for s in self.sessions if s["id"] == eq.id]
            word = STATUS_WORD.get(st, "상태 미확인")
            if mine:
                word = "접속 중" if any(s["connected"] for s in mine) else "접속 시도 중"
            method = check_method(eq)
            how = {"ping": " (ping)", "off": " (확인 안 함)"}.get(method, "")
            hint = ("ping 이 막힌 설비면: 오른쪽 클릭 > 상태 확인 > 하지 않음"
                    if method == "ping" and st is False and not mine else "")
            parts = [eq.name, eq.host if eq.port == 5900 else f"{eq.host}:{eq.port}", eq.os,
                     eq.group or NO_GROUP, word + how, f"▲ {eq.caution}" if eq.caution else "", eq.memo, hint]
            self.foot_var.set("   ·   ".join(p for p in parts if p))

    def _on_motion(self, _event):
        key, _eq_id, zone = self._target()
        self._set_hover((key, zone) if key else (None, None))

    def _on_click(self, event):
        self._settle(None)
        key, eq_id, zone = self._target()
        if not key or (key, zone) != self.pressed:
            return
        eq = self._by_id(eq_id)
        if not eq:
            return
        shift, ctrl = event.state & 0x0001, event.state & 0x0004
        if ctrl or shift or self.selecting:   # 여러 대 선택
            if not self.selecting:
                self.selecting = True
            if shift and self.anchor:
                self._select_range(eq.id)
            else:
                self.selected ^= {eq.id}
                self.anchor = eq.id
            self._selection_changed()
        elif zone == "share":
            self.open_share(eq)
        elif zone == "rdp":
            self.open_rdp(eq)
        else:
            self.connect(eq)

    def _on_right(self, event):
        _key, eq_id, _zone = self._target()
        eq = self._by_id(eq_id)
        if not eq:
            return
        if self.selecting and eq.id in self.selected and len(self.selected) > 1:
            menu = self._bulk_menu(sorted(self.selected))
        else:
            menu = self._single_menu(eq)
        menu.tk_popup(event.x_root, event.y_root)

    def _single_menu(self, eq: Equipment) -> tk.Menu:
        m = self.menu
        m.delete(0, "end")
        s = self._session(eq.id, "VNC")
        m.add_command(label=f"{eq.name}  " + ("창 앞으로 가져오기" if s and s["connected"] else "VNC 접속"),
                      command=lambda: self.connect(eq))
        if self._rdp_on(eq):
            r = self._session(eq.id, "RDP")
            m.add_command(label="원격 데스크톱 창 앞으로 가져오기" if r and r["connected"]
                          else "원격 데스크톱(RDP) 접속", command=lambda: self.open_rdp(eq))
        if self._share_on(eq):
            m.add_command(label="공유폴더 열기", command=lambda: self.open_share(eq))
        m.add_command(label="IP 복사", command=lambda: self.copy_ip(eq))
        m.add_separator()
        fav = self.local.is_favorite(eq.id)
        m.add_command(label="★ 즐겨찾기에서 빼기" if fav else "☆ 즐겨찾기에 넣기",
                      command=lambda: self.set_favorite([eq.id], not fav))
        self._fill_caution_menu(self.sub_caution, [eq.id])
        m.add_cascade(label="주의 표시" + (f"  ({eq.caution})" if eq.caution else ""), menu=self.sub_caution)
        m.add_separator()
        self._fill_move_menu(self.sub_move, [eq.id])
        m.add_cascade(label="그룹 이동", menu=self.sub_move)
        if eq.os == OS_WINDOWS and (self.cfg.show_share or self.cfg.show_rdp):
            self._fill_buttons_menu(self.sub_buttons, eq)
            m.add_cascade(label="버튼 표시", menu=self.sub_buttons)
        self._fill_check_menu(self.sub_check, [eq.id])
        m.add_cascade(label=f"상태 확인  ({CHECK_SHORT[eq.check]})", menu=self.sub_check)
        if secret.AVAILABLE and self.cfg.supports_password():
            m.add_command(label="VNC 비밀번호 저장…", command=lambda: self.set_password(eq))
        m.add_command(label="수정…", command=lambda: self.edit(eq))
        m.add_command(label="삭제", command=lambda: self.delete([eq.id]))
        m.add_separator()
        m.add_command(label="여러 대 선택하기", command=lambda: self.start_selection(eq.id))
        return m

    def _bulk_menu(self, ids: list[str]) -> tk.Menu:
        m = self.menu
        m.delete(0, "end")
        m.add_command(label=f"{len(ids)}대 선택됨", state="disabled")
        m.add_separator()
        all_fav = all(self.local.is_favorite(i) for i in ids)
        m.add_command(label="★ 즐겨찾기에서 빼기" if all_fav else "☆ 즐겨찾기에 넣기",
                      command=lambda: self.set_favorite(ids, not all_fav))
        self._fill_caution_menu(self.sub_caution, ids)
        m.add_cascade(label="주의 표시", menu=self.sub_caution)
        self._fill_move_menu(self.sub_move, ids)
        m.add_cascade(label="그룹 이동", menu=self.sub_move)
        self._fill_check_menu(self.sub_check, ids)
        m.add_cascade(label="상태 확인", menu=self.sub_check)
        m.add_separator()
        m.add_command(label=f"{len(ids)}대 삭제", command=lambda: self.delete(ids))
        m.add_command(label="선택 끝내기", command=self.end_selection)
        return m

    def _fill_move_menu(self, menu: tk.Menu, ids: list[str]):
        menu.delete(0, "end")
        groups = {e.group for e in (self._by_id(i) for i in ids) if e}
        for g in self.store.groups + [""]:
            menu.add_command(label=("✓ " if groups == {g} else "    ") + (g or NO_GROUP),
                             command=lambda g=g: self.move_to(ids, g))
        menu.add_separator()
        menu.add_command(label="    새 그룹 만들기…", command=lambda: self.move_to_new(ids))

    def _fill_caution_menu(self, menu: tk.Menu, ids: list[str]):
        menu.delete(0, "end")
        current = {e.caution for e in (self._by_id(i) for i in ids) if e}
        for text in CAUTION_PRESETS:
            menu.add_command(label=("✓ " if current == {text} else "    ") + text,
                             command=lambda t=text: self.set_caution(ids, t))
        menu.add_command(label="    직접 입력…", command=lambda: self.ask_caution(ids))
        if any(current):
            menu.add_separator()
            menu.add_command(label="    주의 표시 지우기", command=lambda: self.set_caution(ids, ""))

    def _fill_check_menu(self, menu: tk.Menu, ids: list[str]):
        menu.delete(0, "end")
        current = {e.check for e in (self._by_id(i) for i in ids) if e}
        for key, label in CHECK_METHODS.items():
            menu.add_command(label=("✓ " if current == {key} else "    ") + label,
                             command=lambda k=key: self.set_check(ids, k))

    def _fill_buttons_menu(self, menu: tk.Menu, eq: Equipment):
        menu.delete(0, "end")
        if self.cfg.show_share:
            menu.add_command(label=("✓ " if eq.share_enabled else "    ") + "공유폴더 버튼",
                             command=lambda: self.set_buttons([eq.id], share_enabled=not eq.share_enabled))
        if self.cfg.show_rdp:
            menu.add_command(label=("✓ " if eq.rdp_enabled else "    ") + "원격 데스크톱 버튼",
                             command=lambda: self.set_buttons([eq.id], rdp_enabled=not eq.rdp_enabled))

    def toggle_only_down(self):
        self.only_down = not self.only_down
        self.canvas.yview_moveto(0)
        self.draw()
        if self.only_down:
            self.toast("응답 없는 설비만 보고 있습니다. ping 이 막힌 설비라면: Ctrl+클릭 → [모두 고르기] → "
                       "[상태 확인 ▾] → 하지 않음")

    def _on_enter(self, _event):
        shown = self.visible()
        if len(shown) == 1:
            self.connect(shown[0])
        elif shown:
            self.toast(f"설비 {len(shown)}대가 보입니다. 한 대만 남도록 검색하면 Enter로 바로 접속합니다.")

    def _on_search_escape(self, _event):
        """Esc 는 한 겹씩: 검색어가 있으면 검색어만 지우고, 없으면 여러 대 선택을 끝낸다."""
        if self.search_var.get():
            self.search_var.set("")
            return "break"
        return None

    def _on_search_delete(self, _event):
        """검색 칸에는 늘 커서가 있다. 검색 칸이 글자를 지우기 전에 판단한다:
        지울 글자가 있으면(커서 뒤에 글자가 있거나 글자를 골라 두었으면) 글자를 지우고,
        없으면 고른 설비를 삭제한다."""
        if not (self.selecting and self.selected):
            return None
        s = self.search
        if s.selection_present() or s.index("insert") < len(self.search_var.get()):
            return None
        self.delete(sorted(self.selected))
        return "break"

    def _on_delete_key(self, event):
        if event.widget is self.search:   # 검색 칸에서 누른 것은 _on_search_delete 가 이미 판단했다
            return
        if self.selecting and self.selected:
            self.delete(sorted(self.selected))

    def toast(self, text: str):
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self.foot_var.set(text)
        self._toast_job = self.root.after(4000, self._toast_done)

    def _toast_done(self):
        self._toast_job = None
        self._foot()

    # ----------------------------------------------------- 여러 대 선택 ---

    def start_selection(self, eq_id: str | None = None):
        self.selecting = True
        if eq_id:
            self.selected.add(eq_id)
            self.anchor = eq_id
        self._selection_changed()

    def end_selection(self):
        self.selecting = False
        self.selected.clear()
        self.anchor = None
        self._selection_changed()

    def select_all_visible(self):
        self.selected.update(e.id for e in self.visible())
        self._selection_changed()

    def _select_range(self, eq_id: str):
        if self.anchor in self.order and eq_id in self.order:
            i, j = sorted((self.order.index(self.anchor), self.order.index(eq_id)))
            self.selected.update(self.order[i:j + 1])
        else:
            self.selected.add(eq_id)
        self.anchor = eq_id

    def _sel_text(self) -> str:
        n = len(self.selected)
        if n:
            return f"{n}대 선택"
        return "고르세요" if self.sel_compact else "설비를 클릭해서 고르세요"

    def _fit_selbar(self):
        """선택 바가 창 폭에 다 들어가지 않으면 버튼을 짧은 이름으로 바꾼다."""
        avail = self.sel_row.winfo_width()
        if avail <= 1:
            return
        names = {id(b): (full, short) for b, full, short in self.sel_names}
        buttons = [b for b, _f, _s in self.sel_names] + [b for b in self.sel_buttons
                                                          if id(b) not in names]

        def need(compact: bool) -> int:
            total = self.f_group.measure(f"{len(self.selected)}대 선택" if self.selected else
                                         ("고르세요" if compact else "설비를 클릭해서 고르세요"))
            for b in buttons:
                text = names[id(b)][1 if compact else 0] if id(b) in names else b.text
                total += self.f_body.measure(text) + 30 + 6
            return total + 24

        compact = need(False) > avail
        if compact != self.sel_compact:
            self.sel_compact = compact
            for b, full, short in self.sel_names:
                b.set_text(short if compact else full)
            self.sel_label["text"] = self._sel_text()

    def _sync_selbar(self):
        self.selected &= {e.id for e in self.items}
        if not self.selecting:
            self.selbar.pack_forget()
            return
        n = len(self.selected)
        self.sel_label["text"] = self._sel_text()
        for b in self.sel_buttons:
            if b.enabled != bool(n):
                b.set_enabled(bool(n))
        if not self.selbar.winfo_manager():
            self.selbar.pack(side="bottom", fill="x", before=self.body)

    def _selection_changed(self):
        self._sync_selbar()
        self.draw()

    def favorite_selected(self):
        ids = sorted(self.selected)
        self.set_favorite(ids, not all(self.local.is_favorite(i) for i in ids))

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
        self.log = ConnectionLog(self.store.folder())

    def reload(self, quiet: bool = False) -> bool:
        self.seen_mtime = self.store.mtime()
        try:
            self.items = self.store.load()
        except StoreError as e:
            if quiet:   # 저절로 다시 읽을 때 (네트워크가 잠깐 끊긴 경우가 대부분): 보던 목록을 그대로 둔다
                self.toast("설비 목록 파일에 연결할 수 없습니다. 연결되면 다시 읽습니다.")
            else:
                messagebox.showerror(APP_NAME, str(e))
            return False
        if self.group not in [g for g, _n in self._groups()]:
            self.group = ALL_GROUPS
        self._sync_selbar()
        self._draw_chips()
        self.draw()
        return True

    def _watch_file(self):
        # 다른 PC가 공유 목록을 바꾸면 따라간다.
        if self.store.mtime() != self.seen_mtime:
            self.reload(quiet=True)
        self.root.after(5000, self._watch_file)

    def _write_log(self, action: str, name: str, host: str, note: str = ""):
        if self.cfg.connection_log:
            self.log_pool.submit(self.log.write, action, name, host, note)

    # -------------------------------------------------------- 편집 잠금 ---

    def _writable(self) -> bool:
        """이 계정이 설비 목록 파일을 바꿀 수 있는지. 못 바꾸면 알리고 False.

        관리자만 쓸 수 있게 폴더 권한을 정해 둔 공유 목록이면, 부서원 PC 는 읽기 전용으로 돈다."""
        if not self.store.read_only:
            return True
        writable = self.store.check_writable()   # 그사이 권한이 바뀌었을 수 있다
        self._summary()
        if writable:
            return True
        messagebox.showinfo(APP_NAME, (
            "이 계정에는 설비 목록 파일을 바꿀 권한이 없습니다 (읽기 전용).\n"
            f"{self.store.path}\n\n"
            "접속, 즐겨찾기, 비밀번호 저장은 그대로 쓸 수 있습니다. 목록을 바꾸려면 관리자에게 요청하세요."))
        return False

    def _can_edit(self, write: bool = True) -> bool:
        """설비 목록을 바꿔도 되는지. 잠겨 있으면 PIN 을 묻는다.
        write=False: 목록 파일은 바꾸지 않는 관리자 동작 (배포본 만들기 등) - PIN 만 확인한다."""
        if write and not self._writable():
            return False
        if not self.store.lock:
            return True
        if self.unlocked_at:
            self.unlocked_at = time.monotonic()   # 편집하는 동안은 다시 잠기지 않는다
            return True
        pin = simpledialog.askstring("편집 잠금", "설비 목록이 잠겨 있습니다.\n관리자 PIN을 입력하세요.",
                                     show="*", parent=self.root)
        if pin is None:
            return False
        if not check_pin(self.store.lock, pin):
            messagebox.showwarning(APP_NAME, wrong_pin_text(pin))
            return False
        self.unlocked_at = time.monotonic()
        self._summary()
        self.toast(f"편집 잠금을 풀었습니다. {RELOCK_SECONDS // 60}분 동안 편집하지 않으면 다시 잠깁니다.")
        return True

    def is_admin(self) -> bool:
        """관리자 메뉴(배포본 만들기·업데이트 올리기·편집 잠금 설정)를 늘 보일지.
        정하지 않았으면: 배포본으로 설치한 PC 는 부서원 PC, 아니면 관리자 PC."""
        if self.local.admin is not None:
            return self.local.admin
        return not self.local.applied_pack

    def set_admin(self, on: bool):
        if on and not self._can_edit(write=False):
            return
        self.local.admin = on
        self.local.save()
        self.toast("관리자 메뉴를 [더보기]에 늘 보입니다." if on else
                   "관리자 메뉴를 숨겼습니다. 다시 보려면 Shift를 누른 채 [더보기]를 누르세요.")

    def relock(self):
        self.unlocked_at = 0.0
        self._summary()
        self.toast("편집을 다시 잠갔습니다.")

    def open_lock(self):
        if not self._writable():
            return
        has_lock = bool(self.store.lock)
        dlg = LockDialog(self.root, has_lock)

        def save(current: str, new: str | None) -> bool:
            if has_lock and not check_pin(self.store.lock, current):
                messagebox.showwarning(APP_NAME, wrong_pin_text(current), parent=dlg.top)
                return False
            try:
                self.store.set_lock(new)
            except StoreError as e:
                messagebox.showerror(APP_NAME, str(e), parent=dlg.top)
                return False
            self.unlocked_at = 0.0
            self.reload()
            self.toast("편집 잠금을 껐습니다." if new is None else
                       "편집 잠금을 걸었습니다. 설비 추가·수정·삭제와 그룹 변경 때 PIN을 묻습니다.")
            return True

        dlg.on_save = save

    def _tick_clock(self):
        """15초마다: 오래 편집하지 않았으면 다시 잠그고, 때가 되면 상태를 다시 확인한다."""
        now = time.monotonic()
        if self.unlocked_at and now - self.unlocked_at > RELOCK_SECONDS:
            self.unlocked_at = 0.0
            self._summary()
            self.toast(f"{RELOCK_SECONDS // 60}분 동안 편집하지 않아 편집을 다시 잠갔습니다.")
        minutes = self.cfg.auto_check_minutes
        # 창을 내려 둔 동안은 아무도 보지 않으므로 설비를 두드리지 않는다 (다시 열면 바로 확인)
        if (minutes and self.items and self.pending == 0 and now - self.last_check >= minutes * 60 - 1
                and self.root.state() != "iconic"):
            self.check_status(quiet=True)
        if now - self.last_update_check >= UPDATE_CHECK_HOURS * 3600:   # 며칠씩 켜 둔 PC 도 새 버전을 알도록
            self.check_update(manual=False)
        self.root.after(15000, self._tick_clock)

    def _on_map(self, event):
        """창을 다시 열었을 때: 내려 둔 동안 쉬었던 자동 확인을 바로 한다."""
        if event.widget is not self.root:
            return
        minutes = self.cfg.auto_check_minutes
        if minutes and self.items and self.pending == 0 and time.monotonic() - self.last_check >= minutes * 60 - 1:
            self.check_status(quiet=True)

    # ------------------------------------------------------------ 동작 ---

    def _session(self, eq_id: str, kind: str) -> dict | None:
        return next((s for s in self.sessions if s["id"] == eq_id and s["kind"] == kind), None)

    def _reuse(self, eq: Equipment, kind: str) -> bool:
        """이미 떠 있는 창이 있으면 그 창을 앞으로 가져온다. True = 새로 접속하지 않는다."""
        s = self._session(eq.id, kind)
        if not s:
            return False
        if not s["connected"]:
            self.toast(f"{eq.name}: 접속 시도 중입니다. 잠시 기다려 주세요.")
            return True
        if wintitle.focus(s["proc"].pid):
            self.toast(f"{eq.name} {kind} 창을 앞으로 가져왔습니다.")
            return True
        return not messagebox.askyesno(
            APP_NAME, f"{eq.name}에 이미 {kind}로 접속해 있지만 창을 찾지 못했습니다.\n\n"
                      "새로 한 번 더 접속할까요?")

    def _confirm_caution(self, eq: Equipment) -> bool:
        if not eq.caution:
            return True
        return messagebox.askyesno(
            APP_NAME, f"{eq.name} 설비에 주의 표시가 있습니다.\n\n      ▲  {eq.caution}\n\n그래도 접속할까요?",
            icon="warning", default="no")

    def _started(self, eq: Equipment, kind: str, proc: subprocess.Popen):
        first = not any(not s["connected"] for s in self.sessions)
        self.sessions.append({"id": eq.id, "name": eq.name, "host": eq.host, "kind": kind,
                              "proc": proc, "started": time.monotonic(), "connected": False})
        self._write_log(f"{kind} 접속", eq.name, eq.host)
        self._show_sessions()
        self.draw()
        if first:
            self._sweep()

    def connect(self, eq: Equipment | None):
        if not eq or self._reuse(eq, "VNC") or not self._confirm_caution(eq):
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
            self._started(eq, "VNC", proc)
            if secret.AVAILABLE and self.cfg.supports_password() and not pw:
                self.toast(f"{eq.name}: 저장된 비밀번호가 없어 뷰어가 비밀번호를 묻습니다. "
                           "(오른쪽 클릭 > VNC 비밀번호 저장)")
            else:
                self.toast(f"{eq.name} ({eq.host}) 접속 시도 중…")

    def open_rdp(self, eq: Equipment | None):
        if not eq or not self._rdp_on(eq):
            return
        if sys.platform != "win32":
            messagebox.showinfo(APP_NAME, f"Windows에서만 열 수 있습니다.\n{' '.join(rdp_command(eq))}")
            return
        if self._reuse(eq, "RDP") or not self._confirm_caution(eq):
            return
        if not self.rdp_warned:
            if not messagebox.askyesno(APP_NAME, (
                    f"{eq.name}에 원격 데스크톱으로 접속합니다.\n\n"
                    "VNC와 달리 원격 데스크톱은 설비 앞의 화면을 잠그고, 설비에 로그인해 있던 사람의 "
                    "연결을 끊을 수 있습니다. 설비가 운전 중이면 VNC로 접속하세요.\n\n계속할까요?"),
                    icon="warning", default="no"):
                return
            self.rdp_warned = True   # 프로그램을 켤 때마다 한 번만 묻는다
        try:
            proc = launch(rdp_command(eq))
        except OSError as e:
            messagebox.showerror(APP_NAME, f"원격 데스크톱 연결을 실행할 수 없습니다.\n({e})")
        else:
            self._started(eq, "RDP", proc)
            self.toast(f"{eq.name} ({eq.host}) 원격 데스크톱 접속 시도 중… 계정은 Windows가 묻습니다.")

    def _tick_sessions(self):
        """창 상태를 살핀다: 창이 떴는지(접속 시도 중 -> 접속 중), 닫혔는지. 창 제목에 설비명도 붙인다."""
        changed = False
        alive = []
        for s in self.sessions:
            if s["proc"].poll() is not None:       # 창이 닫힘 (접속 실패 포함)
                changed = True
                if not s["connected"]:
                    self.toast(f"{s['name']}: 접속하지 못했습니다. ({s['kind']} 창이 뜨기 전에 닫혔습니다)")
                    self._write_log(f"{s['kind']} 실패", s["name"], s["host"], "창이 뜨기 전에 닫힘")
                else:
                    minutes = round((time.monotonic() - s["started"]) / 60)
                    self._write_log(f"{s['kind']} 종료", s["name"], s["host"], f"{minutes}분")
                continue
            alive.append(s)
            windows = 0
            try:
                windows = wintitle.retitle(s["proc"].pid, s["name"], rename=self.cfg.title_with_name)
            except Exception:   # 창을 못 살펴도 접속에는 지장이 없다
                pass
            if not s["connected"]:
                waited = time.monotonic() - s["started"]
                # 창이 화면에 나타나면 시도가 끝난 것으로 본다. 창을 살필 수 없는 경우를
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

    def _place_sweep(self, key: str):
        """접속 시도 중인 버튼 아래쪽에서 좌우로 오가는 막대의 지금 위치."""
        box = self.boxes.get(key)
        if not box:
            return
        x, y, w = box
        span, bar = w - 2 * RADIUS, 34
        t = (time.monotonic() % 1.4) / 0.7           # 0~2: 갔다가 돌아온다
        pos = ease(t if t <= 1 else 2 - t) * (span - bar)
        left = x + RADIUS + pos
        self.canvas.coords(f"sweep:{key}", left, y + self.th - 4, left + bar, y + self.th - 2)

    def _sweep(self):
        if not any(not s["connected"] for s in self.sessions):
            return
        for key in list(self.boxes):
            self._place_sweep(key)
        self.root.after(30, self._sweep)

    def _show_sessions(self):
        self._draw_chips()

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
            self._write_log("공유폴더 열기", eq.name, eq.host, path)
            self.toast(f"{eq.name} 공유폴더를 엽니다: {path}")

    def copy_ip(self, eq: Equipment | None):
        if eq:
            self.root.clipboard_clear()
            self.root.clipboard_append(eq.host)
            self.toast(f"IP를 복사했습니다: {eq.host}")

    def _what(self, ids: list[str]) -> str:
        """토스트에 쓸 대상 이름: 한 대면 설비명, 여러 대면 "N대"."""
        if len(ids) == 1:
            e = self._by_id(ids[0])
            return f"{e.name} 설비를" if e else "설비를"
        return f"설비 {len(ids)}대를"

    def set_favorite(self, ids: list[str], on: bool):
        if self.local.set_favorite(ids, on):
            self.local.save()
            self.toast(f"{self._what(ids)} 즐겨찾기에 {'넣었' if on else '뺐'}습니다.")
        self.draw()

    def set_caution(self, ids: list[str], text: str):
        """주의 표시는 PIN 없이 바꿀 수 있다 - 현장 상황을 알리는 표시라서."""
        if not self._writable():
            return
        try:
            changed = self.store.set_fields(ids, caution=text)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        else:
            if changed:
                self.toast(f"{self._what(ids)} " + (f"'▲ {text}'(으)로 표시했습니다." if text
                                                    else "주의 표시에서 뺐습니다."))
        self.reload()

    def ask_caution(self, ids: list[str]):
        current = {e.caution for e in (self._by_id(i) for i in ids) if e}
        text = simpledialog.askstring(
            "주의 표시", f"설비 버튼에 표시하고, 접속할 때 한 번 더 물을 내용\n(최대 {CAUTION_MAX}자, 비우면 지웁니다)",
            initialvalue=current.pop() if len(current) == 1 else "", parent=self.root)
        if text is not None:
            self.set_caution(ids, text.strip())

    def set_check(self, ids: list[str], method: str):
        """상태 확인 방식을 바꾸고, 바꾼 설비는 새 방식으로 바로 다시 확인한다."""
        eqs = [e for e in (self._by_id(i) for i in ids) if e]
        if not eqs or not self._can_edit():   # 권한·PIN 을 먼저 - 경고에 답한 뒤 PIN 에서 막히지 않게
            return
        linux = [e.name for e in eqs if e.os == "Linux"]
        if method == "port" and linux and not messagebox.askyesno(APP_NAME, (
                "리눅스 VNC 서버(TigerVNC 등)는 포트 확인을 '보안 실패'로 세어, 5번이 쌓이면 이 PC 의 접속을 막습니다\n"
                f"(\"Too many security failures\").\n\n리눅스 설비: {self._join(linux)}\n\n그래도 VNC 포트로 확인할까요?"),
                icon="warning", default="no"):
            return
        try:
            changed = self.store.set_fields([e.id for e in eqs], check=method)
        except StoreError as e:
            messagebox.showwarning(APP_NAME, str(e))
            self.reload()
            return
        for e in eqs:
            self.status.pop(e.id, None)   # 예전 방식의 결과는 버린다
        self.reload()
        if changed:
            who = eqs[0].name if len(eqs) == 1 else f"설비 {len(eqs)}대"
            self.toast(f"{who}의 상태 확인을 '{CHECK_SHORT[method]}'(으)로 바꿨습니다.")
        self.check_status([e for e in self.items if e.id in {x.id for x in eqs}], quiet=True)
        self.draw()

    def set_buttons(self, ids: list[str], **fields):
        if not self._can_edit():
            return
        try:
            self.store.set_fields(ids, **fields)
        except StoreError as e:
            messagebox.showwarning(APP_NAME, str(e))
        else:
            what = "공유폴더" if "share_enabled" in fields else "원격 데스크톱"
            shown = next(iter(fields.values()))
            self.toast(f"{self._what(ids)} {what} 버튼을 {'보이게' if shown else '숨기게'} 했습니다.")
        self.reload()

    def set_password(self, eq: Equipment):
        pw = simpledialog.askstring(
            "VNC 비밀번호", f"{eq.name} 의 VNC 비밀번호\n이 PC에만 암호화해 저장합니다. 비워 두면 저장된 것을 지웁니다.",
            show="*", parent=self.root)
        if pw is None:
            return
        try:
            self.passwords.set(eq.id, pw)
        except OSError as e:
            messagebox.showwarning(APP_NAME, f"비밀번호를 저장하지 못했습니다.\n({e})")
            return
        self.toast(f"{eq.name} 비밀번호를 저장했습니다." if pw else f"{eq.name} 비밀번호를 지웠습니다.")

    def move_to(self, ids: list[str], group: str):
        if not self._can_edit():
            return
        try:
            moved = self.store.move(ids, group)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        else:
            if moved:
                self.toast(f"{self._what(ids)} '{group or NO_GROUP}'(으)로 옮겼습니다.")
        self.reload()

    def move_to_new(self, ids: list[str]):
        if not self._can_edit():
            return
        name = ask_group_name(self.root, "새 그룹 만들기", "", self.store.groups)
        if name:
            self.move_to(ids, name)

    def open_groups(self):
        if not self._can_edit():
            return
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
        if not self._can_edit():
            return
        group = "" if self.group in (ALL_GROUPS, NO_GROUP) else self.group
        self._edit_dialog(Equipment(name="", host="", group=group), is_new=True)

    def edit(self, eq: Equipment | None):
        if eq and self._can_edit():
            self._edit_dialog(eq, is_new=False)

    def _edit_dialog(self, eq: Equipment, is_new: bool):
        groups = list(self.store.groups)
        has_pw = secret.AVAILABLE and not is_new and self.passwords.has(eq.id)
        dlg = EquipmentDialog(self.root, eq, is_new, groups, has_pw, self.cfg.supports_password())

        def save(new: Equipment, password):
            try:
                if is_new:
                    self.store.add(new)
                else:   # 창을 연 뒤 다른 PC가 바꾼 항목(주의 표시 등)은 지킨다
                    self.store.update(new, base=eq)
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

    def delete(self, ids: list[str]):
        eqs = [e for e in (self._by_id(i) for i in ids) if e]
        if not eqs or not self._can_edit():
            return
        if len(eqs) == 1:
            question = f"설비를 목록에서 삭제할까요?\n\n{eqs[0].name}  ({eqs[0].host})"
        else:
            names = "\n".join(e.name for e in eqs[:10]) + (f"\n외 {len(eqs) - 10}대" if len(eqs) > 10 else "")
            question = f"설비 {len(eqs)}대를 목록에서 삭제할까요?\n\n{names}"
        if not messagebox.askyesno(APP_NAME, question, icon="warning", default="no"):
            return
        gone = [e.id for e in eqs]
        try:
            self.store.delete(gone)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        else:
            try:
                self.passwords.delete(gone)
            except OSError:
                pass
            self.local.forget(gone)
            self.local.save()
            self.toast(f"{self._what(gone)} 삭제했습니다. (backup 폴더에 그날 처음 상태가 남아 있습니다)")
        self.reload()

    def import_csv(self):
        path = filedialog.askopenfilename(title="CSV 가져오기",
                                          filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            changes_list = read_csv(path)[1]   # IP 열이 있으면 설비 목록을 바꾼다
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        if changes_list and not self._can_edit():
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
            return f"비밀번호를 저장하지 못했습니다. 설정 폴더(더보기 > 설정 폴더 열기)의 쓰기 권한을 확인하세요.\n({e})"
        text = (f"비밀번호 저장 {len(passwords)}대 - 이 PC에 암호화해 기억했으므로 "
                "다시 가져올 필요가 없습니다.\n\n"
                "가져온 파일에는 비밀번호가 그대로 적혀 있으니 안전한 곳에 두거나 지우세요.")
        if not self.cfg.supports_password():
            text += "\n\n지금 설정된 뷰어는 비밀번호 자동 입력을 지원하지 않아, 접속할 때 뷰어가 직접 묻습니다."
        return text

    def export_csv(self):
        items = sorted(self.items, key=lambda e: (e.group, natural_key(e.name)))
        saved = [e for e in items if secret.AVAILABLE and self.passwords.has(e.id)]
        passwords = None
        if saved:
            answer = messagebox.askyesnocancel(APP_NAME, (
                f"저장해 둔 VNC 비밀번호({len(saved)}대)도 함께 내보낼까요?\n\n"
                "예: 마지막 열에 비밀번호를 넣습니다. 다른 PC에서 [CSV 가져오기]로 설비와 비밀번호를 "
                "한 번에 넣을 수 있습니다. 파일에는 비밀번호가 그대로 적히므로 쓴 뒤에는 지우세요.\n"
                "아니요: 설비 정보만 내보냅니다."), icon="question", default="no")
            if answer is None:
                return
            if answer:
                if not self._can_edit():      # 편집 잠금이 있으면 비밀번호를 꺼내기 전에 PIN 을 묻는다
                    return
                passwords = {e.id: pw for e in saved if (pw := self.passwords.get(e.id))}
        path = filedialog.asksaveasfilename(
            title="CSV 내보내기", defaultextension=".csv",
            initialfile="설비목록_비밀번호포함.csv" if passwords is not None else "설비목록.csv",
            filetypes=[("CSV 파일", "*.csv")])
        if not path:
            return
        try:
            export_csv(items, path, passwords)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        if passwords is None:
            self.toast(f"CSV로 내보냈습니다: {path}")
            return
        missing = len(saved) - len(passwords)
        messagebox.showinfo(APP_NAME, (
            f"설비 {len(items)}대와 비밀번호 {len(passwords)}대를 내보냈습니다.\n{path}\n\n"
            + (f"비밀번호 {missing}대는 이 PC·이 계정에서 풀 수 없어 빈칸으로 두었습니다.\n\n" if missing else "")
            + "이 파일에는 비밀번호가 그대로 적혀 있습니다. 옮긴 뒤에는 지우세요."))

    def _open_path(self, path: str):
        if sys.platform != "win32":
            messagebox.showinfo(APP_NAME, f"Windows에서만 열 수 있습니다.\n{path}")
            return
        try:
            os.startfile(path)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"열 수 없습니다.\n{path}\n({e})")

    def open_history(self):
        try:
            copy = self.log.view_copy()
        except OSError as e:
            messagebox.showerror(APP_NAME, f"접속 이력을 임시 폴더로 복사하지 못했습니다.\n"
                                           f"{self.log.path()}\n({e})")
            return
        if copy:
            self._open_path(copy)
        elif not self.cfg.connection_log:
            messagebox.showinfo(APP_NAME, "접속 이력 남기기가 꺼져 있습니다.\n[더보기 > 설정]에서 켤 수 있습니다.")
        else:
            messagebox.showinfo(APP_NAME, f"이번 달 접속 이력이 아직 없습니다.\n{self.log.path()}")

    # ----------------------------------------------------------- 배포본 ---

    def make_pack(self):
        """지금 설정·설비 목록·비밀번호를 넣은 exe 를 만든다 (빌드 도구 없이)."""
        src = own_exe()
        if not src:
            messagebox.showinfo(APP_NAME, "배포본은 EquipmentRemote.exe 로 실행했을 때만 만들 수 있습니다.")
            return
        if not self._can_edit(write=False):
            return
        with_pw = [e.id for e in self.items if secret.AVAILABLE and self.passwords.has(e.id)]
        dlg = PackDialog(self.root, self.cfg.equipment_file, len(self.items), len(with_pw))

        def save(include_pw: bool, passphrase: str) -> bool:
            dst = filedialog.asksaveasfilename(
                parent=dlg.top, title="배포본 저장", defaultextension=".exe",
                initialfile=f"설비원격접속_배포_{datetime.date.today():%Y%m%d}.exe",
                filetypes=[("실행 파일", "*.exe")])
            if not dst:
                return False
            equipment = None if self.cfg.equipment_file else {
                "groups": list(self.store.groups), "items": [dataclasses.asdict(e) for e in self.items]}
            passwords = None
            if include_pw:
                passwords = {i: p for i in with_pw if (p := self.passwords.get(i))}
            try:
                write_pack(src, dst, make_payload(dataclasses.asdict(self.cfg), equipment, passwords, passphrase,
                                                  update_key=self._signing_public() or self._trusted_key()))
            except PackError as e:
                messagebox.showerror(APP_NAME, str(e), parent=dlg.top)
                return False
            parts = ["공유 목록 경로" if self.cfg.equipment_file else f"설비 {len(self.items)}대",
                     "뷰어 설정"] + ([f"비밀번호 {len(passwords)}대"] if passwords else [])
            key = self._signing_public() or self._trusted_key()
            if key:
                parts.append(f"업데이트 서명 키 {fingerprint(key)}")
            messagebox.showinfo(APP_NAME, (
                f"배포본을 만들었습니다.\n{dst}\n\n들어 있는 것: {', '.join(parts)}\n\n"
                "이 파일 하나를 나눠 주면, 받은 사람은 실행만 하면 같은 상태로 시작합니다."
                + ("\n배포 암호는 따로(말이나 메신저로) 알려 주세요." if passwords and passphrase else "")),
                parent=dlg.top)
            return True

        dlg.on_save = save

    # ---------------------------------------------------------- 업데이트 ---

    def _bg(self, work, done):
        """work() 를 일꾼 스레드에서 돌리고, 끝나면 화면 스레드에서 done(결과, 오류) 를 부른다."""
        def run():
            try:
                self.jobs.put((done, work(), None))
            except Exception as e:   # UpdateError 등 - 화면 쪽에서 사용자에게 보인다
                self.jobs.put((done, None, e))
        self.bg_pool.submit(run)

    def _trusted_key(self) -> str:
        """이 PC가 믿는 업데이트 서명 키. 빌드 때 넣은 키가 있으면 그것, 없으면 이 PC가 기억한 키."""
        return (UPDATE_PUBLIC_KEY or self.local.update_key or "").strip().lower()

    def _key_store(self) -> SigningKeyStore:
        return SigningKeyStore(os.path.join(app_dir(), KEY_FILE))

    def _signing_public(self) -> str:
        """이 PC에 있는 업데이트 서명 키(관리자 PC)의 공개 키. 없으면 ""."""
        try:
            return self._key_store().public_hex()
        except Exception:
            return ""

    def _pin_key(self, key: str):
        key = (key or "").strip().lower()
        if len(key) != 64 or any(ch not in "0123456789abcdef" for ch in key):
            return
        if not UPDATE_PUBLIC_KEY and self.local.update_key != key:
            self.local.update_key = key
            self.local.save()

    def check_update(self, manual: bool):
        """업데이트 폴더의 latest.json 을 일꾼 스레드에서 읽고 서명을 확인한다 (공유 폴더가 느려도 화면이 멈추지 않게)."""
        folder = self.cfg.update_folder
        if not folder:
            if manual:
                messagebox.showinfo(APP_NAME, "업데이트 폴더가 정해지지 않았습니다.\n"
                                              "[더보기 > 설정]에서 '업데이트 폴더'를 지정하세요.")
            return
        if self.updating:
            if manual:
                self.toast("업데이트를 받는 중입니다. 잠시 기다려 주세요.")
            return
        if self.update_checking:       # 확인이 이미 돌고 있다 - 그 결과를 쓴다 (직접 누른 것이면 결과를 보인다)
            self.update_manual = self.update_manual or manual
            return
        self.update_checking, self.update_manual = True, manual
        self.last_update_check = time.monotonic()
        trusted = self._trusted_key()

        def work():
            info = read_latest(folder)
            return (info, verify_info(info, trusted) if info else ("unsigned", ""))

        def done(result, error):
            self.update_checking = False
            self._update_checked(result, error, self.update_manual)

        self._bg(work, done)

    def _update_checked(self, result, error, manual: bool):
        if self.updating:              # 그사이 받기·올리기를 시작했다 - 버튼을 건드리지 않는다
            return
        if error is not None and not isinstance(error, UpdateError):
            error = UpdateError(f"업데이트 정보를 확인하지 못했습니다.\n({error})")
        info, (verdict, key) = result if result else (None, ("unsigned", ""))
        if info and verdict == "trust_new":
            self._pin_key(key)   # 처음 본 서명 키를 기억한다 - 앞으로는 이 키로 서명한 것만 받는다
        # 1.4.7 부터 관리자는 늘 서명해 올리므로 서명 없는 것은 받지 않는다 (믿는 키를 잃은 PC 라도)
        trusted_ok = verdict in ("ok", "trust_new")
        newer = bool(info) and is_newer(info["version"], APP_VERSION)
        self.update_info = info if newer and trusted_ok else None
        if self.update_info:
            self.update_btn.set_text(f"업데이트  v{info['version']}")
            self.update_btn.set_enabled(True)
            if not self.update_btn.winfo_manager():
                self.update_btn.pack(side="right", padx=(0, 6))
            if not manual:
                self.toast(f"새 버전 v{info['version']}이 있습니다. 위쪽의 [업데이트]를 누르세요.")
        elif not self.updating:
            self.update_btn.pack_forget()
        rejected = bool(info) and not trusted_ok and newer
        if rejected and not manual and self.update_rejected != info["version"]:
            self.update_rejected = info["version"]
            self.toast(f"서버의 v{info['version']}은 서명이 맞지 않아 받지 않았습니다. 관리자에게 알려 주세요.")
        if not manual:
            return
        if error is not None:
            messagebox.showwarning(APP_NAME, str(error))
        elif rejected:
            messagebox.showwarning(APP_NAME, self._rejected_text(info, key))
        elif self.update_info:
            self.start_update()
        else:
            messagebox.showinfo(APP_NAME, f"최신 버전입니다. (지금 v{APP_VERSION}"
                                + (f", 서버 v{info['version']})" if info else ", 서버에 올린 버전 없음)"))

    def _rejected_text(self, info: dict, key: str) -> str:
        trusted = self._trusted_key()
        if not info.get("signature"):
            why = "서명이 없습니다."
        elif key and trusted and key.lower() != trusted:
            why = f"서명 키가 다릅니다.\n  이 PC가 믿는 키: {fingerprint(trusted)}\n  서버 파일의 키: {fingerprint(key)}"
        else:
            why = "서명이 내용과 맞지 않습니다 (올린 뒤 파일이 바뀌었습니다)."
        return (f"서버에 올라온 v{info.get('version', '?')}을(를) 받지 않았습니다.\n{why}\n\n"
                "관리자가 올린 것이 아니거나, 올린 뒤 파일이 바뀌었을 수 있습니다. 관리자에게 알려 주세요.\n"
                "(관리자가 서명 키를 바꾼 경우에는 새 배포본을 받아 한 번 실행하면 됩니다)")

    def start_update(self):
        info = self.update_info
        if not info or self.updating:
            return
        exe = own_exe()
        if not exe:
            messagebox.showinfo(APP_NAME, "업데이트는 EquipmentRemote.exe 로 실행했을 때만 됩니다.")
            return
        notes = f"\n\n바뀐 점:\n{info['notes']}" if info.get("notes") else ""
        if not messagebox.askyesno(APP_NAME, (
                f"v{APP_VERSION} → v{info['version']} 로 업데이트할까요?{notes}\n\n"
                "프로그램이 잠깐 꺼졌다가 새 버전으로 다시 켜집니다. 설정·즐겨찾기·비밀번호는 그대로입니다."
                + ("\n\n열려 있는 VNC 창은 그대로 둡니다." if self.sessions else ""))):
            return
        self.updating = True
        self.update_btn.set_enabled(False, "받는 중…")
        self.toast(f"v{info['version']}을(를) 받는 중입니다…")
        folder = self.cfg.update_folder
        self._bg(lambda: install(folder, info, exe, app_dir()),
                 lambda target, error: self._update_installed(info, exe, target, error))

    def _update_installed(self, info: dict, exe: str, target, error):
        if error is not None:
            self.updating = False
            self.update_btn.set_enabled(True, f"업데이트  v{info['version']}")
            messagebox.showerror(APP_NAME, str(error))
            return
        if os.path.abspath(target) != os.path.abspath(exe):
            messagebox.showinfo(APP_NAME, (
                "지금 exe 가 있는 폴더에는 쓸 수 없어서, 새 버전을 아래에 받았습니다.\n"
                f"{target}\n\n앞으로는 이 파일로 실행하세요. (바탕화면 바로가기를 이 파일로 바꾸면 편합니다)"))
        try:
            relaunch(target)
        except OSError as e:
            self.updating = False
            self.update_btn.pack_forget()   # 새 exe 는 이미 놓였다 - 다시 받을 것은 없다
            messagebox.showerror(APP_NAME, f"새 버전을 켜지 못했습니다. 직접 실행하세요.\n{target}\n({e})")
            return
        self.close()

    def publish_update(self):
        """지금 실행 중인 exe 를 새 버전으로 업데이트 폴더에 올린다 (관리자). 이 PC의 서명 키로 서명한다."""
        exe = own_exe()
        if not exe:
            messagebox.showinfo(APP_NAME, "업데이트 올리기는 EquipmentRemote.exe 로 실행했을 때만 됩니다.")
            return
        folder = self.cfg.update_folder
        if not folder:
            messagebox.showinfo(APP_NAME, "먼저 [더보기 > 설정]에서 '업데이트 폴더'를 지정하세요.")
            return
        if self.updating:
            return
        if not self._can_edit(write=False):
            return
        try:
            current = read_latest(folder)
        except UpdateError as e:
            if not messagebox.askyesno(APP_NAME, f"{e}\n\n그래도 이 폴더에 올릴까요?", default="no"):
                return
            current = None
        seed = self._seed_for_publish(current)
        if seed is None:
            return
        if current and not is_newer(APP_VERSION, current["version"]) and not messagebox.askyesno(
                APP_NAME, f"서버에는 이미 v{current['version']}이 있습니다. 지금 것은 v{APP_VERSION} 입니다.\n\n"
                          "그래도 올릴까요? (받는 PC들은 서버 것이 지금 것보다 새것일 때만 업데이트합니다)",
                icon="warning", default="no"):
            return
        notes = simpledialog.askstring("업데이트 올리기", f"v{APP_VERSION} 을(를) 올립니다.\n{folder}\n\n"
                                       "바뀐 점을 짧게 적으세요. (받는 사람에게 보입니다, 비워도 됩니다)",
                                       parent=self.root)
        if notes is None:
            return
        self.updating = True
        self.root.configure(cursor="watch")
        self.toast(f"v{APP_VERSION}을(를) 올리는 중입니다…")
        self._bg(lambda: publish(exe, folder, APP_VERSION, notes, signing_seed=seed),
                 lambda info, error: self._published(folder, info, error))

    def _published(self, folder: str, info, error):
        self.updating = False
        self.root.configure(cursor="")
        if error is not None:
            messagebox.showerror(APP_NAME, str(error))
            return
        self._pin_key(info.get("key", ""))   # 관리자 PC도 자기 키로 서명한 것만 받는다
        messagebox.showinfo(APP_NAME, f"v{info['version']}을(를) 올렸습니다.\n{folder}\n"
                                      f"서명 키: {fingerprint(info.get('key', ''))}\n\n"
                                      "각 PC는 프로그램을 켤 때(또는 [더보기 > 업데이트 확인]) 업데이트 버튼이 보입니다.")

    def _seed_for_publish(self, current: dict | None) -> bytes | None:
        """올릴 때 쓸 서명 키. 없으면 만든다. 서버의 것과 다른 키로 올리려 하면 먼저 묻는다. None = 그만둔다."""
        store = self._key_store()
        server_key = str((current or {}).get("key") or "").lower()
        if store.exists():
            try:
                seed = store.load()
            except UpdateError as e:
                messagebox.showerror(APP_NAME, str(e))
                return None
            mine = store.public_hex()
            if server_key and server_key != mine and not messagebox.askyesno(APP_NAME, (
                    "서버의 업데이트는 이 PC와 다른 서명 키로 올라와 있습니다.\n"
                    f"  서버: {fingerprint(server_key)}\n  이 PC: {fingerprint(mine)}\n\n"
                    "이 PC의 키로 올리면, 서버 키를 믿는 PC들은 이 업데이트를 받지 않습니다 "
                    "(새 배포본을 다시 나눠 줘야 합니다).\n\n그래도 이 PC의 키로 올릴까요?"),
                    icon="warning", default="no"):
                return None
            return seed
        if server_key:
            if not messagebox.askyesno(APP_NAME, (
                    f"서버의 업데이트는 서명 키 {fingerprint(server_key)} 로 올라와 있는데, 이 PC에는 서명 키가 없습니다.\n\n"
                    "원래 키를 가진 관리자 PC에서 올리거나, [관리자 > 업데이트 서명 키 > 백업에서 가져오기]로 "
                    "그 키를 이 PC에 가져온 뒤 올리세요.\n\n"
                    "새 키를 만들어 올리면 다른 PC들은 이 업데이트를 받지 않습니다 (새 배포본을 다시 나눠 줘야 합니다).\n"
                    "새 키를 만들어 올릴까요?"), icon="warning", default="no"):
                return None
        else:
            messagebox.showinfo(APP_NAME, (
                "업데이트 서명 키를 만듭니다.\n\n"
                "앞으로 각 PC는 이 키로 서명한 업데이트만 받습니다. 업데이트 폴더에 누가 다른 exe 를 놓아도 받지 않습니다.\n"
                "키는 이 PC, 이 Windows 계정에만 암호화해 둡니다. PC를 바꾸거나 잃을 때를 위해 "
                "바로 다음에 백업 파일을 만들어 두세요."))
        try:
            seed = store.create()
        except UpdateError as e:
            messagebox.showerror(APP_NAME, str(e))
            return None
        self._pin_key(store.public_hex())
        if messagebox.askyesno(APP_NAME, (
                f"서명 키를 만들었습니다: {fingerprint(store.public_hex())}\n\n"
                "지금 백업 파일을 만들까요? (USB 등 이 PC 밖에 보관하세요)")):
            self.export_signing_key(self.root)
        return seed

    def open_signing_key(self):
        """업데이트 서명 키: 지금 상태 보기, 백업 파일 만들기, 백업에서 가져오기 (관리자)."""
        if not self._can_edit(write=False):
            return
        SigningKeyDialog(self.root, self)

    def export_signing_key(self, parent) -> bool:
        store = self._key_store()
        if not store.exists():
            messagebox.showinfo(APP_NAME, "이 PC에는 서명 키가 없습니다. [업데이트 올리기]를 처음 하면 만들어집니다.",
                                parent=parent)
            return False
        phrase = self._ask_new_phrase(parent)
        if phrase is None:
            return False
        dst = filedialog.asksaveasfilename(
            parent=parent, title="서명 키 백업 저장", defaultextension=".json",
            initialfile=f"업데이트서명키_백업_{fingerprint(store.public_hex())}.json",
            filetypes=[("서명 키 백업", "*.json")])
        if not dst:
            return False
        try:
            store.export(dst, phrase)
        except UpdateError as e:
            messagebox.showerror(APP_NAME, str(e), parent=parent)
            return False
        messagebox.showinfo(APP_NAME, (
            f"서명 키를 백업했습니다.\n{dst}\n\n"
            "이 파일과 백업 암호가 있으면 다른 PC에서도 업데이트를 올릴 수 있습니다. "
            "업데이트 폴더나 부서 공유 폴더가 아닌 곳(USB 등)에 두고, 암호는 따로 기억하세요."), parent=parent)
        return True

    def import_signing_key(self, parent) -> bool:
        src = filedialog.askopenfilename(parent=parent, title="서명 키 백업 파일",
                                         filetypes=[("서명 키 백업", "*.json"), ("모든 파일", "*.*")])
        if not src:
            return False
        store = self._key_store()
        replace = False
        for _ in range(3):
            phrase = simpledialog.askstring("서명 키 가져오기", "백업을 만들 때 정한 암호를 입력하세요.",
                                            show="*", parent=parent)
            if phrase is None:
                return False
            try:
                key = store.import_file(src, phrase, replace=replace)
            except ValueError:
                messagebox.showwarning(APP_NAME, HANGUL_KEY_PHRASE if not phrase.isascii()
                                       else "암호가 맞지 않습니다.", parent=parent)
                continue
            except UpdateError as e:
                if replace or not store.exists():
                    messagebox.showerror(APP_NAME, str(e), parent=parent)
                    return False
                if not messagebox.askyesno(APP_NAME, (
                        f"{e}\n\n이 PC의 지금 키({fingerprint(store.public_hex())})를 버리고 "
                        "백업의 키로 바꿀까요?"), icon="warning", default="no", parent=parent):
                    return False
                replace = True
                try:
                    key = store.import_file(src, phrase, replace=True)
                except (UpdateError, ValueError) as e2:
                    messagebox.showerror(APP_NAME, str(e2), parent=parent)
                    return False
            self._pin_key(key)
            messagebox.showinfo(APP_NAME, f"서명 키를 가져왔습니다: {fingerprint(key)}\n"
                                          "이제 이 PC에서 업데이트를 올릴 수 있습니다.", parent=parent)
            return True
        return False

    def _ask_new_phrase(self, parent) -> str | None:
        while True:
            phrase = simpledialog.askstring("서명 키 백업", "백업 파일을 잠글 암호 (영문·숫자 8자 이상)",
                                            show="*", parent=parent)
            if phrase is None:
                return None
            if not phrase.isascii():
                messagebox.showwarning(APP_NAME, HANGUL_KEY_PHRASE, parent=parent)
                continue
            if len(phrase) < 8:
                messagebox.showwarning(APP_NAME, "암호는 8자 이상이어야 합니다.", parent=parent)
                continue
            again = simpledialog.askstring("서명 키 백업", "같은 암호를 한 번 더 입력하세요.", show="*", parent=parent)
            if again is None:
                return None
            if again != phrase:
                messagebox.showwarning(APP_NAME, "두 번 입력한 암호가 서로 다릅니다.", parent=parent)
                continue
            return phrase

    def _maybe_apply_pack(self, first_run: bool) -> bool:
        """이 exe 가 배포본이면 넣어 둔 내용을 가져온다. 뷰어까지 준비되면 True."""
        pack = read_pack(own_exe())
        if not pack or not pack.get("id") or pack["id"] == self.local.applied_pack:
            return False
        eq = pack.get("equipment")
        what = (f"설비 {len(eq.get('items', []))}대" if eq else "공유 목록 경로") + ", 뷰어 설정"
        if pack.get("password_count"):
            what += f", 비밀번호 {pack['password_count']}대"
        if not first_run and not messagebox.askyesno(APP_NAME, (
                f"이 파일은 배포본입니다. ({pack.get('made', '')}, {pack.get('by', '')})\n"
                f"들어 있는 것: {what}\n\n지금 이 PC에 가져올까요?\n"
                "설정은 배포본 것으로 바뀌고, 설비는 합쳐집니다. 즐겨찾기는 그대로입니다.")):
            self.local.applied_pack = pack["id"]
            self.local.save()
            return False
        ok = self._apply_pack(pack)
        self.local.applied_pack = pack["id"]
        self.local.admin = False          # 배포본을 받은 PC = 부서원 PC (관리자 메뉴 숨김)
        self.local.save()
        return ok

    def _apply_pack(self, pack: dict) -> bool:
        known = {f.name for f in dataclasses.fields(Config)}
        cfg = Config(**{k: v for k, v in (pack.get("config") or {}).items() if k in known})
        if not os.path.isfile(cfg.viewer_path):   # 받는 PC는 뷰어가 다른 곳에 깔렸을 수 있다
            found = detect_viewer(prefer=cfg.viewer_type)
            if found:
                cfg.viewer_type, cfg.viewer_path = found
        self.cfg = cfg
        self._save_config()
        self._open_store()
        lines = []
        eq = pack.get("equipment")
        if eq:
            try:
                added, updated = self.store.merge(eq.get("items") or [], eq.get("groups") or [])
                lines.append(f"설비 목록: 추가 {added}대, 바뀜 {updated}대")
            except StoreError as e:
                lines.append(f"설비 목록을 가져오지 못했습니다: {e}")
        else:
            lines.append(f"설비 목록: 공유 목록을 씁니다\n    {cfg.equipment_file}")
        sealed = pack.get("passwords")
        if sealed:
            pw = self._unseal(sealed)
            if pw is None:
                lines.append("비밀번호: 가져오지 않았습니다 (배포 암호를 넣지 않음)")
            elif not secret.AVAILABLE:
                lines.append("비밀번호: Windows에서만 저장됩니다")
            else:
                try:
                    self.passwords.set_many({str(k): str(v) for k, v in pw.items() if v})
                    lines.append(f"비밀번호: {len(pw)}대 저장 (이 PC·이 계정에 암호화)")
                except OSError as e:
                    lines.append(f"비밀번호를 저장하지 못했습니다: {e}")
        key = str(pack.get("update_key") or "").strip().lower()
        if len(key) == 64 and all(ch in "0123456789abcdef" for ch in key):
            # 배포본은 관리자가 직접 나눠 준 것이라, 거기 든 서명 키를 이 PC가 믿을 키로 삼는다
            self.local.update_key = key
            lines.append(f"업데이트 서명 키: {fingerprint(key)}")
        ok = os.path.isfile(cfg.viewer_path)
        if not ok:
            lines.append("\nVNC 뷰어를 찾지 못했습니다. 다음 설정 화면에서 뷰어 실행 파일을 지정하세요.")
        messagebox.showinfo(APP_NAME, "배포본의 설정을 가져왔습니다.\n\n" + "\n".join(lines))
        return ok

    def _unseal(self, sealed: dict) -> dict | None:
        if not sealed.get("passphrase"):
            try:
                return unseal(sealed, "")
            except ValueError:
                return None
        for _ in range(3):
            phrase = simpledialog.askstring(
                "배포 암호", "이 배포본의 VNC 비밀번호는 배포 암호로 잠겨 있습니다.\n"
                             "배포한 사람에게 받은 암호를 입력하세요. (취소하면 비밀번호 없이 시작)",
                show="*", parent=self.root)
            if phrase is None:
                return None
            try:
                return unseal(sealed, phrase)
            except ValueError:
                messagebox.showwarning(APP_NAME, HANGUL_PHRASE if not phrase.isascii()
                                       else "배포 암호가 맞지 않습니다.")
        return None

    def open_guide(self):
        """exe 안에 넣어 둔 사용법을 임시 폴더에 꺼내 메모장으로 연다."""
        try:
            dst = os.path.join(tempfile.gettempdir(), f"설비원격접속_사용법_v{APP_VERSION}.txt")
            shutil.copyfile(resource_path(GUIDE), dst)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"사용법 파일을 꺼내지 못했습니다.\n({e})")
            return
        self._open_path(dst)

    def save_templates(self):
        """CSV 양식(설비 목록, 비밀번호)을 고른 폴더에 저장한다."""
        folder = filedialog.askdirectory(title="CSV 양식을 저장할 폴더")
        if not folder:
            return
        saved = []
        try:
            for name in TEMPLATES:
                dst = os.path.join(folder, name)
                if os.path.exists(dst) and not messagebox.askyesno(
                        APP_NAME, f"이미 있는 파일입니다. 덮어쓸까요?\n{dst}", default="no"):
                    continue
                shutil.copyfile(resource_path(name), dst)
                saved.append(name)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"양식을 저장하지 못했습니다.\n({e})")
            return
        if saved:
            messagebox.showinfo(APP_NAME, "저장했습니다. 엑셀로 열어 고친 뒤 [더보기 > CSV 가져오기]로 가져오세요.\n\n"
                                + "\n".join(os.path.join(folder, n) for n in saved))

    def open_backups(self):
        folder = self.store.backup_dir()
        if os.path.isdir(folder):
            self._open_path(folder)
        else:
            messagebox.showinfo(APP_NAME, (
                "아직 백업이 없습니다.\n"
                f"설비 목록을 바꾸는 날마다 처음 바꾸기 직전 상태를 {BACKUP_KEEP}일치 남깁니다.\n{folder}"))

    def open_settings(self, first: bool = False):
        intro = ("처음 실행하셨습니다. VNC 뷰어와 설비 목록 위치를 확인하고 [저장]을 누르세요.\n"
                 "부서가 함께 쓰는 목록이 있으면 '설비 목록 파일'에 그 경로를 넣습니다.") if first else ""
        dlg = SettingsDialog(self.root, self.cfg, intro)

        def save(cfg: Config):
            old = self.cfg
            self.cfg = cfg
            if not self._save_config():
                self.cfg = old
                return False
            self._open_store()
            self.status.clear()
            self.checked_at = ""
            self._show_hint()
            self.check_update(manual=False)
            if self.reload():
                self.check_status()
            return True

        dlg.on_save = save

    # ------------------------------------------------------- 상태 확인 ---

    def check_status(self, items=None, quiet: bool = False):
        """설비의 VNC 포트 응답을 확인한다. quiet = 자동 확인 (버튼과 화면을 건드리지 않는다)."""
        items = list(self.items if items is None else items)
        if not items:
            return
        # 확인 중인 설비는 또 두드리지 않는다 (F5 를 누르고 있으면 설비마다 수십 번 두드리게 된다)
        items = [e for e in items if e.id not in self.inflight]
        if not items:
            return
        timeout = self.cfg.check_timeout
        for e in items:
            method = check_method(e)
            if method == "off":   # 확인하지 않는 설비: 상태를 비워 둔다 (미확인)
                self.status.pop(e.id, None)
                continue
            if not quiet and self.status.get(e.id) is None:
                self.status[e.id] = "busy"
            self.pending += 1
            self.inflight.add(e.id)
            self.pool.submit(self._probe, e.id, e.host, e.port, timeout, method)
        if not self.pending:
            return
        if not quiet:
            self.check_btn.set_enabled(False, "확인 중…")
            self.draw()

    def _probe(self, eq_id: str, host: str, port: int, timeout: float, method: str = "port"):
        """일꾼 스레드에서 돈다. 무슨 일이 있어도 결과를 하나 남겨야 확인이 끝난다."""
        try:
            ok = probe(method, host, port, timeout)
        except Exception:
            ok = False
        self.results.put((eq_id, host, port, ok))

    def _poll_results(self):
        changed = got = False
        by_id = {e.id: e for e in self.items}
        while True:
            try:
                eq_id, host, port, ok = self.results.get_nowait()
            except queue.Empty:
                break
            got = True
            self.pending -= 1
            self.inflight.discard(eq_id)
            cur = by_id.get(eq_id)
            if cur and (cur.host, cur.port) == (host, port):
                if self.status.get(eq_id) is True and ok is False:
                    self.newly_down.append(cur.name)
                changed = changed or self.status.get(eq_id) != ok
                self.status[eq_id] = ok
        if changed:
            self.draw()
        while True:   # 업데이트 확인·받기·올리기 같은 일꾼 스레드 일이 끝났다
            try:
                done, result, error = self.jobs.get_nowait()
            except queue.Empty:
                break
            try:
                done(result, error)
            except Exception:   # 화면 스레드의 마무리에서 난 오류가 확인 주기를 멈추지 않게
                traceback.print_exc()
        if got and self.pending <= 0:   # 한 번의 확인이 끝났다
            self.pending = 0
            self.inflight.clear()
            self.last_check = time.monotonic()
            self.checked_at = time.strftime("%H:%M")
            self._summary()
            if self.newly_down:
                self.toast("응답 없음으로 바뀐 설비: " + self._join(self.newly_down))
                self.newly_down = []
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
        # 모든 모니터를 합친 영역 안으로 (왼쪽·위쪽 모니터는 좌표가 음수다)
        t = self.top
        vx, vy, vw, vh = t.winfo_vrootx(), t.winfo_vrooty(), t.winfo_vrootwidth(), t.winfo_vrootheight()
        x = max(vx, min(x, vx + vw - t.winfo_width()))
        y = max(vy, min(y, vy + vh - t.winfo_height()))
        self.top.geometry(f"+{x}+{y}")
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
        self.caution = tk.StringVar(value=eq.caution)
        self.check = tk.StringVar(value=CHECK_METHODS[eq.check])
        self.share_on = tk.BooleanVar(value=eq.share_enabled)
        self.rdp_on = tk.BooleanVar(value=eq.rdp_enabled)
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
        row = ttk.Frame(b)
        self.share_check = ttk.Checkbutton(row, text="공유폴더 버튼", variable=self.share_on,
                                           command=self._sync)
        self.share_check.pack(side="left", padx=(0, 18))
        self.rdp_check = ttk.Checkbutton(row, text="원격 데스크톱(RDP) 버튼", variable=self.rdp_on)
        self.rdp_check.pack(side="left")
        self.field("버튼 표시", row, "Windows 설비에만 해당. 전체 사용 여부는 [더보기 > 설정]")
        self.field("주의 표시", ttk.Combobox(b, textvariable=self.caution, values=("",) + CAUTION_PRESETS),
                   "예: PM 중. 적어 두면 버튼에 ▲ 표시가 붙고, 접속할 때 한 번 더 묻습니다")
        self.field("메모", ttk.Entry(b, textvariable=self.memo))
        self.field("상태 확인", ttk.Combobox(b, textvariable=self.check, values=list(CHECK_METHODS.values()),
                                          state="readonly"),
                   "리눅스 VNC 서버는 포트 확인을 실패로 세어 접속을 막으므로 ping 으로 확인합니다")
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
        self.rdp_check["state"] = "normal" if is_win else "disabled"
        self.share_entry["state"] = "normal" if is_win and self.share_on.get() else "disabled"

    def submit(self):
        is_win = self.os.get() == OS_WINDOWS
        new = Equipment.from_dict({
            "id": self.eq.id, "name": self.name.get(), "group": self.group.get(),
            "host": self.host.get(), "port": self.port.get() or "5900", "os": self.os.get(),
            "share": self.share.get() if is_win else "", "memo": self.memo.get(),
            "share_enabled": self.share_on.get(), "caution": self.caution.get(),
            "rdp_enabled": self.rdp_on.get(),
            "check": next(k for k, v in CHECK_METHODS.items() if v == self.check.get())})
        password = None  # None = 건드리지 않음
        if self.pw_clear.get():
            password = ""
        elif self.pw.get():
            password = self.pw.get()
        if self.on_save and self.on_save(new, password):
            self.top.destroy()


AUTO_CHECK_CHOICES = {"끔": 0, "1분마다": 1, "3분마다": 3, "5분마다": 5, "10분마다": 10}


class SettingsDialog(_Dialog):
    def __init__(self, parent, cfg: Config, intro: str = ""):
        super().__init__(parent, "처음 설정" if intro else "설정")
        self.cfg = cfg
        b = self.body
        if intro:
            ttk.Label(b, text=intro, foreground=ACCENT_DARK, justify="left").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
            self._row = 1
        self.keys = list(VIEWERS)
        self.vtype = tk.StringVar(value=VIEWERS[cfg.viewer_type]["label"])
        self.vpath = tk.StringVar(value=cfg.viewer_path)
        self.extra = tk.StringVar(value=cfg.extra_args)
        self.cargs = tk.StringVar(value=cfg.custom_args)
        self.cpw = tk.StringVar(value=cfg.custom_pw_args)
        self.efile = tk.StringVar(value=cfg.equipment_file)
        self.ufolder = tk.StringVar(value=cfg.update_folder)
        self.check = tk.BooleanVar(value=cfg.check_on_start)
        self.show_share = tk.BooleanVar(value=cfg.show_share)
        self.show_rdp = tk.BooleanVar(value=cfg.show_rdp)
        self.title_name = tk.BooleanVar(value=cfg.title_with_name)
        self.quiet = tk.BooleanVar(value=cfg.quiet_connect)
        self.log_on = tk.BooleanVar(value=cfg.connection_log)
        self.auto_choices = dict(AUTO_CHECK_CHOICES)
        if cfg.auto_check_minutes not in self.auto_choices.values():   # config.ini 에 직접 적은 값
            self.auto_choices[f"{cfg.auto_check_minutes}분마다"] = cfg.auto_check_minutes
        self.auto = tk.StringVar(value=next(k for k, v in self.auto_choices.items()
                                            if v == cfg.auto_check_minutes))

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
                   "비우면 이 PC에만 둡니다. 공유 폴더 경로를 넣으면 부서가 같은 목록을 같이 씁니다")
        row3 = ttk.Frame(b)
        ttk.Entry(row3, textvariable=self.ufolder).pack(side="left", fill="x", expand=True)
        ttk.Button(row3, text="찾기", width=6, command=self._browse_update).pack(side="left", padx=(4, 0))
        self.field("업데이트 폴더", row3, "관리자가 새 버전을 올리는 서버 폴더. 비우면 업데이트를 확인하지 않습니다")

        opts = ttk.Frame(b)

        def check(text, var, r, col):
            ttk.Checkbutton(opts, text=text, variable=var).grid(row=r, column=col, sticky="w",
                                                                padx=(0, 28), pady=3)

        check("프로그램을 켤 때 상태 확인", self.check, 0, 0)
        auto = ttk.Frame(opts)
        auto.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(auto, text="자동 상태 확인").pack(side="left", padx=(0, 8))
        ttk.Combobox(auto, textvariable=self.auto, values=list(self.auto_choices), state="readonly",
                     width=9).pack(side="left")
        check("VNC 창 제목에 설비명 표시", self.title_name, 1, 0)
        check("접속 도중 뜨는 상태창 숨기기 (UltraVNC)", self.quiet, 1, 1)
        check("공유폴더 버튼 사용", self.show_share, 2, 0)
        check("원격 데스크톱(RDP) 버튼 사용", self.show_rdp, 2, 1)
        check("접속 이력 남기기", self.log_on, 3, 0)
        self.field("동작", opts, "설비별로 버튼을 끄려면 설비를 오른쪽 클릭 > 버튼 표시. "
                               "비밀번호 입력창은 비밀번호를 저장해 두면 뜨지 않습니다")
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

    def _browse_update(self):
        path = filedialog.askdirectory(parent=self.top, title="업데이트 폴더")
        if path:
            self.ufolder.set(os.path.normpath(path))

    def _detect(self):
        key = self._key()
        found = detect_viewer(prefer=None if key == "custom" else key)
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
            quiet_connect=self.quiet.get(), show_rdp=self.show_rdp.get(),
            auto_check_minutes=self.auto_choices.get(self.auto.get(), self.cfg.auto_check_minutes),
            connection_log=self.log_on.get(), update_folder=self.ufolder.get().strip().strip('"'))
        if self.on_save and self.on_save(cfg):
            self.top.destroy()


class PackDialog(_Dialog):
    """배포본 만들기: 무엇을 넣을지, 배포 암호."""

    def __init__(self, parent, shared: str, n_items: int, n_pw: int):
        super().__init__(parent, "배포본 만들기")
        b = self.body
        self.include_pw = tk.BooleanVar(value=n_pw > 0)
        self.phrase = tk.StringVar()
        self.again = tk.StringVar()
        ttk.Label(b, foreground=SUB, justify="left", text=(
            "지금 이 PC의 설정과 설비 목록, VNC 비밀번호를 넣은 exe 를 만듭니다.\n"
            "받은 사람은 그 exe 를 실행만 하면 같은 상태로 시작합니다.")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._row = 1
        self.field("설비 목록", ttk.Label(b, text=(f"공유 목록 경로를 넣습니다: {shared}" if shared
                                                 else f"지금 목록 {n_items}대를 복사해 넣습니다")))
        self.field("뷰어 설정", ttk.Label(b, text="넣습니다 (받는 PC에서 뷰어 위치가 다르면 자동으로 찾습니다)"))
        pw = ttk.Checkbutton(b, text=f"VNC 비밀번호 넣기 (저장된 {n_pw}대)", variable=self.include_pw,
                             command=self._sync)
        if not n_pw:
            pw["state"] = "disabled"
        self.field("비밀번호", pw)
        self.phrase_entry = ttk.Entry(b, textvariable=self.phrase, show="*", width=24)
        self.field("배포 암호", self.phrase_entry,
                   "정하면 받은 사람이 처음 실행할 때 이 암호를 넣어야 비밀번호가 풀립니다 (영문·숫자)")
        self.again_entry = ttk.Entry(b, textvariable=self.again, show="*", width=24)
        self.field("배포 암호 확인", self.again_entry)
        ttk.Label(b, foreground=ALERT_TEXT, justify="left", text=(
            "배포 암호 없이 비밀번호를 넣으면, 이 exe 를 가진 사람은 누구나 비밀번호를 꺼낼 수 있습니다.\n"
            "부서 밖으로 나가지 않게 주의하세요.")).grid(row=self._row, column=0, columnspan=2,
                                                       sticky="w", pady=(6, 0))
        self._row += 1
        self._sync()
        self.buttons("만들기")

    def _sync(self):
        state = "normal" if self.include_pw.get() else "disabled"
        self.phrase_entry["state"] = state
        self.again_entry["state"] = state

    def submit(self):
        include = self.include_pw.get()
        phrase = self.phrase.get() if include else ""
        if include:
            if phrase and not phrase.isascii():
                messagebox.showwarning(APP_NAME, HANGUL_PHRASE, parent=self.top)
                return
            if phrase != self.again.get():
                messagebox.showwarning(APP_NAME, "배포 암호 두 칸이 서로 다릅니다.", parent=self.top)
                return
            if not phrase and not messagebox.askyesno(
                    APP_NAME, "배포 암호 없이 만들면, 이 exe 를 가진 사람은 누구나 VNC 비밀번호를 꺼낼 수 있습니다.\n\n"
                              "그래도 만들까요?", icon="warning", default="no", parent=self.top):
                return
        if self.on_save and self.on_save(include, phrase):
            self.top.destroy()


class LockDialog(_Dialog):
    """편집 잠금 PIN 정하기·바꾸기·끄기."""

    def __init__(self, parent, has_lock: bool):
        super().__init__(parent, "편집 잠금")
        self.has_lock = has_lock
        b = self.body
        self.current = tk.StringVar()
        self.new = tk.StringVar()
        self.again = tk.StringVar()
        self.off = tk.BooleanVar(value=False)
        ttk.Label(b, foreground=SUB, justify="left", text=(
            "잠그면 설비 추가·수정·삭제, 그룹 변경, 버튼 표시 변경 때 PIN을 묻습니다.\n"
            "접속, 주의 표시, 즐겨찾기, 비밀번호 저장은 PIN 없이 쓸 수 있습니다.\n"
            "PIN은 설비 목록 파일에 저장되어, 목록을 함께 쓰는 모든 PC에 적용됩니다.\n"
            "PIN을 잊으면  EquipmentRemote.exe --reset-pin  으로 잠금을 끌 수 있습니다.")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._row = 1
        first = None
        if has_lock:
            first = ttk.Entry(b, textvariable=self.current, show="*", width=24)
            self.field("지금 PIN", first)
        self.new_entry = ttk.Entry(b, textvariable=self.new, show="*", width=24)
        self.field("새 PIN", self.new_entry, f"영문·숫자 {PIN_MIN}자 이상")
        self.again_entry = ttk.Entry(b, textvariable=self.again, show="*", width=24)
        self.field("새 PIN 확인", self.again_entry)
        if has_lock:
            self.field("", ttk.Checkbutton(b, text="편집 잠금 끄기", variable=self.off, command=self._sync))
        self.buttons()
        (first or self.new_entry).focus_set()

    def _sync(self):
        state = "disabled" if self.off.get() else "normal"
        self.new_entry["state"] = state
        self.again_entry["state"] = state

    def submit(self):
        off = self.off.get()
        new = self.new.get()
        if not off:
            if not new.isascii():   # Tk 의 * 칸은 한글 입력기를 끄지 않는다
                messagebox.showwarning(APP_NAME, HANGUL_PIN, parent=self.top)
                return
            if len(new) < PIN_MIN:
                messagebox.showwarning(APP_NAME, f"PIN은 {PIN_MIN}자 이상이어야 합니다.", parent=self.top)
                return
            if new != self.again.get():
                messagebox.showwarning(APP_NAME, "새 PIN 두 칸이 서로 다릅니다.", parent=self.top)
                return
        if self.on_save and self.on_save(self.current.get(), None if off else new):
            self.top.destroy()


class SigningKeyDialog(_Dialog):
    """업데이트 서명 키: 지금 상태와 백업·가져오기 (관리자)."""

    def __init__(self, parent, app: "App"):
        super().__init__(parent, "업데이트 서명 키")
        self.app = app
        b = self.body
        ttk.Label(b, foreground=SUB, justify="left", text=(
            "업데이트를 올릴 때 이 키로 서명합니다. 각 PC는 처음 받은 서명 키(또는 배포본에 든 키)를 기억하고,\n"
            "그 키로 서명한 업데이트만 받습니다. 키를 잃으면 새 배포본을 다시 나눠 줘야 하므로 백업해 두세요.")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._row = 1
        self.mine = ttk.Label(b)
        self.field("이 PC의 서명 키", self.mine)
        self.trusted = ttk.Label(b)
        self.field("이 PC가 믿는 키", self.trusted, "업데이트를 받을 때 확인하는 키")
        bar = ttk.Frame(b)
        bar.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._row += 1
        self.export_btn = ttk.Button(bar, text="백업 파일 만들기…", command=lambda: self._run(app.export_signing_key))
        self.export_btn.pack(side="left")
        ttk.Button(bar, text="백업에서 가져오기…", command=lambda: self._run(app.import_signing_key)).pack(
            side="left", padx=(8, 0))
        close = ttk.Frame(b)
        close.grid(row=self._row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(close, text="닫기", command=self.top.destroy).pack(side="right")
        self._refresh()
        self.top.update_idletasks()
        try:
            self.top.grab_set()
        except tk.TclError:
            pass

    def _run(self, action):
        action(self.top)
        self._refresh()

    def _refresh(self):
        mine = self.app._signing_public()
        trusted = self.app._trusted_key()
        self.mine["text"] = fingerprint(mine) if mine else "없음 (업데이트를 처음 올릴 때 만듭니다)"
        self.trusted["text"] = fingerprint(trusted) if trusted else "아직 없음 (처음 받는 서명 업데이트의 키를 믿습니다)"
        self.export_btn["state"] = "normal" if mine else "disabled"


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


def set_window_icon(root: tk.Tk) -> None:
    """창 제목 표시줄·작업 표시줄 아이콘. 뒤에 여는 대화상자도 같은 아이콘을 쓴다."""
    from .icon_data import PNG
    try:
        root._icons = [tk.PhotoImage(master=root, data=d) for d in PNG]   # 지우면 아이콘이 사라진다
        root.iconphoto(True, *root._icons)
    except tk.TclError:
        pass   # 아이콘이 없어도 프로그램은 돌아야 한다


def main():
    if sys.platform == "win32":
        # 작업 표시줄이 이 창을 python 이 아니라 이 프로그램으로 묶고 아이콘을 보이게 한다 (실행.bat 로 켤 때)
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Daves.EquipmentRemote")
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    set_window_icon(root)
    App(root)
    root.mainloop()

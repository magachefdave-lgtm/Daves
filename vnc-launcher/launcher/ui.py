"""tkinter 화면 - 설비를 카드형 버튼으로 보여 준다."""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, APP_VERSION, secret
from .core import (OS_CHOICES, OS_WINDOWS, VIEWERS, Config, Equipment,
                   EquipmentStore, StoreError, app_dir, build_command,
                   detect_viewer, equipment_file, export_csv, is_reachable,
                   load_config, save_config, share_path)

ALL_GROUPS = "전체"
NO_GROUP = "그룹 없음"

# 색
BG = "#f3f5f8"
CARD = "#ffffff"
CARD_HOVER = "#f0f6ff"
BORDER = "#dfe4ea"
TEXT = "#18212c"
SUB = "#6a7686"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4fd7"
SOFT = "#e9eef5"
SOFT_HOVER = "#dbe4f0"
UP = "#16a34a"
DOWN = "#dc2626"
IDLE = "#b6bfca"
STATUS_COLOR = {True: UP, False: DOWN}
STATUS_WORD = {True: "접속 가능", False: "응답 없음", "busy": "확인 중"}

TILE_MIN_W, TILE_H, GAP, PAD = 230, 78, 12, 20


def launch(cmd: list[str]) -> None:
    # 콘솔 없는 exe에서는 표준 핸들이 유효하지 않아 넘겨주지 않으면 WinError 6이 난다.
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


def natural_key(text: str):
    return [(0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r"(\d+)", text)]


def round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    # 꼭짓점 앞뒤 점을 두 번씩 넣어야 변이 곧게 그려진다.
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
           x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class FlatButton(tk.Label):
    """테두리 없는 평면 버튼."""

    def __init__(self, parent, text, command, primary=False, font=None):
        self.colors = (ACCENT, ACCENT_DARK, "#ffffff") if primary else (SOFT, SOFT_HOVER, TEXT)
        super().__init__(parent, text=text, bg=self.colors[0], fg=self.colors[2], font=font,
                         padx=14, pady=7, cursor="hand2")
        self.command, self.enabled = command, True
        self.bind("<Enter>", lambda _e: self.enabled and self.configure(bg=self.colors[1]))
        self.bind("<Leave>", lambda _e: self.enabled and self.configure(bg=self.colors[0]))
        self.bind("<ButtonRelease-1>", lambda _e: self.enabled and self.command())

    def set_enabled(self, flag: bool, text: str | None = None):
        self.enabled = flag
        self.configure(bg=self.colors[0], fg=self.colors[2] if flag else SUB,
                       cursor="hand2" if flag else "")
        if text:
            self.configure(text=text)


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
        self.menu_target: Equipment | None = None
        self.pool = ThreadPoolExecutor(max_workers=16)
        self.results: queue.Queue = queue.Queue()
        self.pending = 0
        self.seen_mtime = 0.0
        self._draw_job = self._toast_job = None

        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.geometry("1100x700")
        root.minsize(720, 420)
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
        if self.cfg.check_on_start:
            self.check_status()
        self.root.after(300, self._poll_results)
        self.root.after(5000, self._watch_file)

    # ------------------------------------------------------------ 화면 ---

    def _fonts(self):
        family = "맑은 고딕" if sys.platform == "win32" else tkfont.nametofont("TkDefaultFont").actual("family")
        f = lambda size, weight="normal": tkfont.Font(family=family, size=size, weight=weight)
        self.f_title, self.f_name = f(15, "bold"), f(11, "bold")
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
        head = tk.Frame(root, bg=CARD)
        head.pack(fill="x")
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x")

        row1 = tk.Frame(head, bg=CARD)
        row1.pack(fill="x", padx=PAD, pady=(16, 10))
        self.more_btn = FlatButton(row1, "더보기  ▾", self._show_more, font=self.f_body)
        self.more_btn.pack(side="right")
        self.check_btn = FlatButton(row1, "상태 확인", self.check_status, font=self.f_body)
        self.check_btn.pack(side="right", padx=8)
        FlatButton(row1, "＋ 설비 추가", self.add, primary=True, font=self.f_group).pack(side="right")

        tk.Label(row1, text=APP_NAME, font=self.f_title, bg=CARD, fg=TEXT).pack(side="left")
        self.summary = tk.Label(row1, font=self.f_body, bg=CARD, fg=SUB)
        self.summary.pack(side="left", padx=14, pady=(4, 0))
        row2 = tk.Frame(head, bg=CARD)
        row2.pack(fill="x", padx=PAD, pady=(0, 14))
        box = tk.Frame(row2, bg=BG, highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=ACCENT)
        box.pack(side="left")
        tk.Label(box, text="검색", bg=BG, fg=SUB, font=self.f_small).pack(side="left", padx=(10, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.draw())
        self.search = tk.Entry(box, textvariable=self.search_var, width=24, relief="flat", bg=BG,
                               fg=TEXT, font=self.f_body, insertbackground=TEXT, highlightthickness=0)
        self.search.pack(side="left", ipady=6, padx=(0, 10))
        self.search.focus_set()
        self.chips = tk.Canvas(row2, bg=CARD, height=32, highlightthickness=0)
        self.chips.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.chips.bind("<Configure>", lambda _e: self._draw_chips())
        self.chips.bind("<Button-1>", self._on_chip)

        foot = tk.Frame(root, bg=BG)
        foot.pack(side="bottom", fill="x")
        self.foot_var = tk.StringVar()
        tk.Label(foot, textvariable=self.foot_var, bg=BG, fg=SUB, font=self.f_small,
                 anchor="w").pack(fill="x", padx=PAD, pady=(2, 8))

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)
        self.canvas = c = tk.Canvas(body, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        c.pack(fill="both", expand=True)
        c.bind("<Configure>", lambda _e: self.draw_later())
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda _e: self._set_hover((None, None)))
        c.bind("<ButtonPress-1>", lambda _e: setattr(self, "pressed", self.hover))
        c.bind("<ButtonRelease-1>", self._on_click)
        c.bind("<Button-3>", self._on_right)
        c.bind("<MouseWheel>", lambda e: c.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        c.bind("<Button-4>", lambda _e: c.yview_scroll(-1, "units"))
        c.bind("<Button-5>", lambda _e: c.yview_scroll(1, "units"))
        c.configure(yscrollincrement=30)

        root.bind("<F5>", lambda _e: (self.reload(), self.check_status()))
        root.bind("<Control-f>", lambda _e: self.search.focus_set())
        self.search.bind("<Escape>", lambda _e: self.search_var.set(""))
        self.search.bind("<Return>", self._on_enter)

        menu_opts = dict(tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                         activeforeground="#ffffff", relief="flat", bd=1)
        self.menu = tk.Menu(root, **menu_opts)
        self.menu.add_command(label="VNC 접속", command=lambda: self.connect(self.menu_target))
        self.menu.add_command(label="공유폴더 열기", command=lambda: self.open_share(self.menu_target))
        self.menu.add_command(label="IP 복사", command=lambda: self.copy_ip(self.menu_target))
        self.menu.add_separator()
        self.menu.add_command(label="수정", command=lambda: self.edit(self.menu_target))
        self.menu.add_command(label="삭제", command=lambda: self.delete(self.menu_target))
        self.more_menu = tk.Menu(root, **menu_opts)
        self.more_menu.add_command(label="CSV 가져오기 (설비 · 비밀번호)", command=self.import_csv)
        self.more_menu.add_command(label="CSV 내보내기", command=self.export_csv)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="설정", command=self.open_settings)

    def _show_more(self):
        b = self.more_btn
        self.more_menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 2)

    # ------------------------------------------------------ 그룹 칩 ---

    def _groups(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for e in self.items:
            counts[e.group] = counts.get(e.group, 0) + 1
        named = sorted((g for g in counts if g), key=natural_key)
        out = [(ALL_GROUPS, len(self.items))] + [(g, counts[g]) for g in named]
        if "" in counts and named:
            out.append((NO_GROUP, counts[""]))
        return out

    def _draw_chips(self):
        c = self.chips
        c.delete("all")
        groups = self._groups()
        if len(groups) <= 1:
            c.configure(height=1)
            return
        width = max(c.winfo_width(), 200)
        x = y = 0
        for name, count in groups:
            label = f"{name}  {count}"
            w = self.f_body.measure(label) + 26
            if x and x + w > width:
                x, y = 0, y + 36
            on = name == self.group
            tag = f"chip:{name}"
            round_rect(c, x, y + 1, x + w, y + 31, 15, fill=ACCENT if on else SOFT, tags=(tag,))
            c.create_text(x + w / 2, y + 16, text=label, font=self.f_body,
                          fill="#ffffff" if on else TEXT, tags=(tag,))
            x += w + 8
        c.configure(height=y + 32)

    def _on_chip(self, _event):
        for tag in self.chips.gettags("current"):
            if tag.startswith("chip:"):
                self.group = tag[5:]
                self._draw_chips()
                self.canvas.yview_moveto(0)
                self.draw()

    # ------------------------------------------------------ 설비 카드 ---

    def visible(self) -> list[Equipment]:
        words = self.search_var.get().casefold().split()
        out = []
        for e in self.items:
            if self.group != ALL_GROUPS and (e.group or NO_GROUP) != self.group:
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
        self.hover = (None, None)
        width = max(c.winfo_width(), 300)
        shown = self.visible()
        up = sum(1 for e in self.items if self.status.get(e.id) is True)
        self.summary["text"] = f"전체 {len(self.items)}대  ·  접속 가능 {up}대"
        if not self._toast_job:
            self.foot_var.set(f"설비 목록: {self.store.path}")

        if not shown:
            if self.items:
                title, hint = "조건에 맞는 설비가 없습니다", "검색어를 지우거나 다른 그룹을 선택하세요"
            else:
                title, hint = "등록된 설비가 없습니다", "[＋ 설비 추가] 또는 [더보기 > CSV 가져오기]로 설비를 등록하세요"
            c.create_text(width / 2, 120, text=title, font=self.f_title, fill=SUB)
            c.create_text(width / 2, 152, text=hint, font=self.f_body, fill=SUB)
            c.configure(scrollregion=(0, 0, width, 300))
            return

        sections: dict[str, list[Equipment]] = {}
        for e in shown:
            sections.setdefault(e.group, []).append(e)
        names = sorted((g for g in sections if g), key=natural_key) + ([""] if "" in sections else [])
        headers = bool(names and names[0])
        cols = max(1, int((width - 2 * PAD + GAP) // (TILE_MIN_W + GAP)))
        tile_w = (width - 2 * PAD - GAP * (cols - 1)) / cols
        y = PAD
        for g in names:
            group_items = sections[g]
            if headers:
                c.create_text(PAD + 2, y + 8, anchor="w", text=g or NO_GROUP, font=self.f_group, fill=TEXT)
                c.create_text(PAD + 10 + self.f_group.measure(g or NO_GROUP), y + 8, anchor="w",
                              text=f"{len(group_items)}대", font=self.f_small, fill=SUB)
                y += 28
            for i, e in enumerate(group_items):
                self._tile(e, PAD + (i % cols) * (tile_w + GAP), y + (i // cols) * (TILE_H + GAP), tile_w)
            rows = (len(group_items) + cols - 1) // cols
            y += rows * (TILE_H + GAP) + 14
        c.configure(scrollregion=(0, 0, width, y + PAD - 14))

    def _tile(self, e: Equipment, x: float, y: float, w: float):
        c = self.canvas
        st = self.status.get(e.id)
        tid = f"id:{e.id}"
        body = (tid, "zone:body")
        round_rect(c, x, y, x + w, y + TILE_H, 14, fill=CARD, outline=BORDER, width=1,
                   tags=body + (f"card:{e.id}",))
        c.create_oval(x + 16, y + 20, x + 26, y + 30, fill=STATUS_COLOR.get(st, IDLE), outline="", tags=body)
        c.create_text(x + 36, y + 25, anchor="w", font=self.f_name, tags=body,
                      fill=SUB if st is False else TEXT, text=self._fit(e.name, self.f_name, w - 36 - 44))
        # 더보기(⋯)
        more = (tid, "zone:more")
        round_rect(c, x + w - 38, y + 11, x + w - 10, y + 39, 8, fill=CARD, outline="",
                   tags=more + (f"btn:{e.id}:more",))
        c.create_text(x + w - 24, y + 24, text="⋯", font=self.f_name, fill=SUB, tags=more)
        # 둘째 줄: IP, 공유폴더 버튼
        addr = e.host if e.port == 5900 else f"{e.host}:{e.port}"
        right = x + w - 12
        if e.os == OS_WINDOWS:
            bw = self.f_small.measure("공유폴더") + 20
            share = (tid, "zone:share")
            round_rect(c, right - bw, y + 44, right, y + 68, 12, fill=SOFT, outline="",
                       tags=share + (f"btn:{e.id}:share",))
            c.create_text(right - bw / 2, y + 56, text="공유폴더", font=self.f_small, fill=TEXT, tags=share)
            right -= bw + 8
        else:
            ow = self.f_small.measure(e.os)
            c.create_text(right, y + 56, anchor="e", text=e.os, font=self.f_small, fill=SUB, tags=body)
            right -= ow + 8
        word = STATUS_WORD.get(st)
        sub = addr + (f"  ·  {word}" if word and st is not True else "")
        if self.f_small.measure(sub) > right - (x + 36):
            sub = addr
        c.create_text(x + 36, y + 56, anchor="w", font=self.f_small, fill=SUB, tags=body,
                      text=self._fit(sub, self.f_small, right - (x + 36)))

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

    def _set_hover(self, new):
        old = self.hover
        if new == old:
            return
        c = self.canvas
        if old[0]:
            c.itemconfigure(f"card:{old[0]}", fill=CARD, outline=BORDER, width=1)
            c.itemconfigure(f"btn:{old[0]}:more", fill=CARD)
            c.itemconfigure(f"btn:{old[0]}:share", fill=SOFT)
        if new[0]:
            c.itemconfigure(f"card:{new[0]}", fill=CARD_HOVER, outline=ACCENT, width=2)
            c.itemconfigure(f"btn:{new[0]}:more", fill=SOFT_HOVER if new[1] == "more" else CARD_HOVER)
            c.itemconfigure(f"btn:{new[0]}:share", fill="#c9d8ee" if new[1] == "share" else SOFT_HOVER)
        c.configure(cursor="hand2" if new[0] else "")
        self.hover = new

    def _on_motion(self, _event):
        self._set_hover(self._target())

    def _on_click(self, event):
        eq_id, zone = self._target()
        if not eq_id or (eq_id, zone) != self.pressed:
            return
        eq = self._by_id(eq_id)
        if not eq:
            return
        if zone == "share":
            self.open_share(eq)
        elif zone == "more":
            self._popup(eq, event)
        else:
            self.connect(eq)

    def _on_right(self, event):
        eq = self._by_id(self._target()[0])
        if eq:
            self._popup(eq, event)

    def _popup(self, eq: Equipment, event):
        self.menu_target = eq
        self.menu.entryconfigure(1, state="normal" if eq.os == OS_WINDOWS else "disabled")
        self.menu.tk_popup(event.x_root, event.y_root)

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
        self.foot_var.set(f"설비 목록: {self.store.path}")

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
            launch(build_command(self.cfg, eq, pw))
        except StoreError as e:
            messagebox.showwarning(APP_NAME, str(e))
        except OSError as e:
            messagebox.showerror(APP_NAME, (
                "VNC 뷰어를 실행할 수 없습니다.\n"
                f"{self.cfg.viewer_path}\n\n"
                "[더보기 > 설정]에서 뷰어 실행 파일 경로가 맞는지 확인하세요.\n"
                f"({e})"))
        else:
            self.toast(f"{eq.name} ({eq.host}) VNC 접속을 시작했습니다.")

    def open_share(self, eq: Equipment | None):
        if not eq or eq.os != OS_WINDOWS:
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

    def add(self):
        group = "" if self.group in (ALL_GROUPS, NO_GROUP) else self.group
        self._edit_dialog(Equipment(name="", host="", group=group), is_new=True)

    def edit(self, eq: Equipment | None):
        if eq:
            self._edit_dialog(eq, is_new=False)

    def _edit_dialog(self, eq: Equipment, is_new: bool):
        groups = sorted({e.group for e in self.items if e.group}, key=natural_key)
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
        self.share_entry["state"] = "normal" if self.os.get() == OS_WINDOWS else "disabled"

    def submit(self):
        is_win = self.os.get() == OS_WINDOWS
        new = Equipment.from_dict({
            "id": self.eq.id, "name": self.name.get(), "group": self.group.get(),
            "host": self.host.get(), "port": self.port.get() or "5900", "os": self.os.get(),
            "share": self.share.get() if is_win else "", "memo": self.memo.get()})
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
            check_on_start=self.check.get(), check_timeout=self.cfg.check_timeout)
        if self.on_save and self.on_save(cfg):
            self.top.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

"""tkinter 화면."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, APP_VERSION, secret
from .core import (OS_CHOICES, OS_WINDOWS, VIEWERS, Config, Equipment,
                   EquipmentStore, StoreError, app_dir, build_command,
                   detect_viewer, equipment_file, export_csv, is_reachable,
                   load_config, save_config, share_path)

ALL_GROUPS = "전체 그룹"
COLUMNS = (  # (키, 제목, 너비, 정렬)
    ("status", "상태", 90, "w"),
    ("name", "설비명", 170, "w"),
    ("group", "그룹", 110, "w"),
    ("host", "IP", 130, "w"),
    ("port", "포트", 60, "e"),
    ("os", "OS", 80, "w"),
    ("share", "공유폴더", 110, "w"),
    ("memo", "메모", 220, "w"),
)
STATUS_TEXT = {None: "-", True: "● 접속 가능", False: "● 응답 없음", "busy": "확인 중..."}


def launch(cmd: list[str]) -> None:
    # 콘솔 없는 exe에서는 표준 핸들이 유효하지 않아 넘겨주지 않으면 WinError 6이 난다.
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config_path = os.path.join(app_dir(), "config.ini")
        self.cfg = Config()
        self.items: list[Equipment] = []
        self.status: dict[str, object] = {}
        self.sort_key, self.sort_rev = "name", False
        self.pool = ThreadPoolExecutor(max_workers=16)
        self.results: queue.Queue = queue.Queue()
        self.pending = 0
        self.seen_mtime = 0.0

        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.geometry("1040x620")
        root.minsize(820, 420)
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

    def _build(self):
        root = self.root
        style = ttk.Style(root)
        if sys.platform == "win32":
            style.configure(".", font=("맑은 고딕", 10))
            style.configure("Treeview.Heading", font=("맑은 고딕", 10, "bold"))
        style.configure("Treeview", rowheight=26)
        style.configure("Main.TButton", font=("맑은 고딕", 11, "bold") if sys.platform == "win32" else None)

        top = ttk.Frame(root, padding=(10, 10, 10, 6))
        top.pack(fill="x")
        ttk.Label(top, text="검색").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_view())
        entry = ttk.Entry(top, textvariable=self.search_var, width=28)
        entry.pack(side="left", padx=(6, 12))
        entry.focus_set()
        self.group_var = tk.StringVar(value=ALL_GROUPS)
        self.group_box = ttk.Combobox(top, textvariable=self.group_var, state="readonly", width=18)
        self.group_box.pack(side="left")
        self.group_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_view())
        ttk.Button(top, text="설정", command=self.open_settings).pack(side="right")
        self.check_btn = ttk.Button(top, text="상태 확인", command=self.check_status)
        self.check_btn.pack(side="right", padx=6)

        side = ttk.Frame(root, padding=(0, 0, 10, 6))
        side.pack(side="right", fill="y")
        self.vnc_btn = ttk.Button(side, text="VNC 접속", style="Main.TButton", command=self.connect)
        self.vnc_btn.pack(fill="x", ipady=8)
        self.share_btn = ttk.Button(side, text="공유폴더 열기", command=self.open_share)
        self.share_btn.pack(fill="x", pady=(6, 0), ipady=4)
        ttk.Separator(side).pack(fill="x", pady=12)
        ttk.Button(side, text="설비 추가", command=self.add).pack(fill="x")
        self.edit_btn = ttk.Button(side, text="수정", command=self.edit)
        self.edit_btn.pack(fill="x", pady=4)
        self.del_btn = ttk.Button(side, text="삭제", command=self.delete)
        self.del_btn.pack(fill="x")
        ttk.Separator(side).pack(fill="x", pady=12)
        ttk.Button(side, text="CSV 가져오기", command=self.import_csv).pack(fill="x")
        ttk.Button(side, text="CSV 내보내기", command=self.export_csv).pack(fill="x", pady=4)

        self.bar_var = tk.StringVar()
        ttk.Label(root, textvariable=self.bar_var, padding=(10, 4), anchor="w",
                  relief="sunken").pack(side="bottom", fill="x")

        mid = ttk.Frame(root, padding=(10, 0, 10, 6))
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in COLUMNS], show="headings",
                                 selectmode="extended")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "memo"))
        self.tree.tag_configure("up", foreground="#1a7f37")
        self.tree.tag_configure("down", foreground="#b42318")
        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", lambda _e: self.connect())
        self.tree.bind("<Delete>", lambda _e: self.delete())
        self.tree.bind("<Button-3>", self._on_menu)
        root.bind("<F5>", lambda _e: (self.reload(), self.check_status()))
        root.bind("<Control-f>", lambda _e: entry.focus_set())
        root.bind("<Escape>", lambda _e: self.search_var.set(""))

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="VNC 접속", command=self.connect)
        self.menu.add_command(label="공유폴더 열기", command=self.open_share)
        self.menu.add_separator()
        self.menu.add_command(label="IP 복사", command=self.copy_ip)
        self.menu.add_command(label="수정", command=self.edit)
        self.menu.add_command(label="삭제", command=self.delete)

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
        groups = sorted({e.group for e in self.items if e.group})
        self.group_box["values"] = [ALL_GROUPS] + groups
        if self.group_var.get() not in self.group_box["values"]:
            self.group_var.set(ALL_GROUPS)
        self.refresh_view()
        return True

    def _watch_file(self):
        # 다른 PC가 공유 목록을 바꾸면 따라간다.
        if self.store.mtime() != self.seen_mtime:
            self.reload()
        self.root.after(5000, self._watch_file)

    def visible(self) -> list[Equipment]:
        words = self.search_var.get().casefold().split()
        group = self.group_var.get()
        out = []
        for e in self.items:
            if group != ALL_GROUPS and e.group != group:
                continue
            hay = " ".join((e.name, e.group, e.host, e.os, e.memo)).casefold()
            if all(w in hay for w in words):
                out.append(e)
        key = self.sort_key
        if key == "status":
            keyfn = lambda e: {True: 0, False: 1}.get(self.status.get(e.id), 2)
        elif key == "port":
            keyfn = lambda e: e.port
        elif key == "host":
            keyfn = lambda e: [int(p) if p.isdigit() else -1 for p in e.host.split(".")] + [e.host]
        else:
            keyfn = lambda e: str(getattr(e, key)).casefold()
        try:
            out.sort(key=keyfn, reverse=self.sort_rev)
        except TypeError:
            out.sort(key=lambda e: e.host, reverse=self.sort_rev)
        return out

    def refresh_view(self):
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        shown = self.visible()
        for e in shown:
            st = self.status.get(e.id)
            tag = {True: "up", False: "down"}.get(st, "")
            share = (e.share or "(전체)") if e.os == OS_WINDOWS else ""
            self.tree.insert("", "end", iid=e.id, tags=(tag,), values=(
                STATUS_TEXT.get(st, "-"), e.name, e.group, e.host, e.port, e.os, share, e.memo))
        keep = [i for i in selected if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        up = sum(1 for e in self.items if self.status.get(e.id) is True)
        self.bar_var.set(f"전체 {len(self.items)}대  |  표시 {len(shown)}대  |  접속 가능 {up}대"
                         f"  |  목록 파일: {self.store.path}")
        self._update_buttons()

    def sort_by(self, key: str):
        self.sort_rev = not self.sort_rev if self.sort_key == key else False
        self.sort_key = key
        self.refresh_view()

    def selected(self) -> list[Equipment]:
        by_id = {e.id: e for e in self.items}
        return [by_id[i] for i in self.tree.selection() if i in by_id]

    def _update_buttons(self):
        sel = self.selected()
        on = lambda flag: "normal" if flag else "disabled"
        share_ok = len(sel) == 1 and sel[0].os == OS_WINDOWS
        self.vnc_btn["state"] = on(sel)
        self.share_btn["state"] = on(share_ok)
        self.edit_btn["state"] = on(len(sel) == 1)
        self.del_btn["state"] = on(sel)
        self.menu.entryconfigure(0, state=on(sel))
        self.menu.entryconfigure(1, state=on(share_ok))
        self.menu.entryconfigure(3, state=on(len(sel) == 1))
        self.menu.entryconfigure(4, state=on(len(sel) == 1))
        self.menu.entryconfigure(5, state=on(sel))

    def _on_double(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":
            self.connect()

    def _on_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------ 동작 ---

    def connect(self):
        sel = self.selected()
        if not sel:
            return
        if len(sel) > 5 and not messagebox.askyesno(
                APP_NAME, f"설비 {len(sel)}대에 한꺼번에 접속합니다. 계속할까요?"):
            return
        for eq in sel:
            try:
                pw = self.passwords.get(eq.id) if secret.AVAILABLE else ""
                launch(build_command(self.cfg, eq, pw))
            except StoreError as e:
                messagebox.showwarning(APP_NAME, str(e))
                return
            except OSError as e:
                messagebox.showerror(APP_NAME, (
                    "VNC 뷰어를 실행할 수 없습니다.\n"
                    f"{self.cfg.viewer_path}\n\n"
                    "[설정]에서 뷰어 실행 파일 경로가 맞는지 확인하세요.\n"
                    f"({e})"))
                return

    def open_share(self):
        sel = self.selected()
        if len(sel) != 1 or sel[0].os != OS_WINDOWS:
            return
        path = share_path(sel[0])
        if sys.platform != "win32":
            messagebox.showinfo(APP_NAME, f"Windows에서만 열 수 있습니다.\n{path}")
            return
        try:
            # 탐색기에 맡긴다 - 설비가 꺼져 있어도 이 프로그램이 멈추지 않고,
            # 계정/비밀번호가 필요하면 Windows가 직접 묻는다.
            launch(["explorer.exe", path])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"탐색기를 실행할 수 없습니다.\n{path}\n({e})")

    def copy_ip(self):
        sel = self.selected()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(sel[0].host)

    def add(self):
        group = self.group_var.get()
        base = Equipment(name="", host="", group="" if group == ALL_GROUPS else group)
        self._edit_dialog(base, is_new=True)

    def edit(self):
        sel = self.selected()
        if len(sel) == 1:
            self._edit_dialog(sel[0], is_new=False)

    def _edit_dialog(self, eq: Equipment, is_new: bool):
        groups = sorted({e.group for e in self.items if e.group})
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
            if self.tree.exists(new.id):
                self.tree.selection_set(new.id)
                self.tree.see(new.id)
            self.check_status([new])
            return True

        dlg.on_save = save

    def delete(self):
        sel = self.selected()
        if not sel:
            return
        names = "\n".join(e.name for e in sel[:8]) + ("\n..." if len(sel) > 8 else "")
        if not messagebox.askyesno(APP_NAME, f"설비 {len(sel)}대를 목록에서 삭제할까요?\n\n{names}",
                                   icon="warning", default="no"):
            return
        ids = [e.id for e in sel]
        try:
            self.store.delete(ids)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
        else:
            try:
                self.passwords.delete(ids)
            except OSError:
                pass
        self.reload()

    def import_csv(self):
        path = filedialog.askopenfilename(title="CSV 가져오기",
                                          filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            added, updated, problems = self.store.import_csv(path)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self.reload()
        msg = f"추가 {added}대, 수정 {updated}대"
        if problems:
            msg += f"\n\n건너뛴 줄 {len(problems)}개:\n" + "\n".join(problems[:10])
            if len(problems) > 10:
                msg += "\n..."
        (messagebox.showwarning if problems else messagebox.showinfo)(APP_NAME, msg)
        self.check_status()

    def export_csv(self):
        path = filedialog.asksaveasfilename(title="CSV 내보내기", defaultextension=".csv",
                                            initialfile="설비목록.csv",
                                            filetypes=[("CSV 파일", "*.csv")])
        if not path:
            return
        try:
            export_csv(sorted(self.items, key=lambda e: (e.group, e.name)), path)
        except StoreError as e:
            messagebox.showerror(APP_NAME, str(e))

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
            self.status[e.id] = "busy"
            self.pending += 1
            self.pool.submit(lambda e=e: self.results.put(
                (e.id, e.host, e.port, is_reachable(e.host, e.port, timeout))))
        self.check_btn["state"] = "disabled"
        self.refresh_view()

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
                self.status[eq_id] = ok
                changed = True
        if changed:
            self.refresh_view()
        if self.pending <= 0:
            self.pending = 0
            self.check_btn["state"] = "normal"
        self.root.after(300, self._poll_results)


# ------------------------------------------------------------- 대화상자 ---

class _Dialog:
    def __init__(self, parent, title):
        self.on_save = None
        self.top = top = tk.Toplevel(parent)
        top.title(title)
        top.transient(parent)
        top.resizable(False, False)
        self.body = ttk.Frame(top, padding=14)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(1, weight=1)
        self._row = 0
        top.bind("<Escape>", lambda _e: top.destroy())

    def field(self, label, widget, hint=""):
        ttk.Label(self.body, text=label).grid(row=self._row, column=0, sticky="w", pady=4)
        widget.grid(row=self._row, column=1, sticky="ew", padx=(10, 0), pady=4)
        self._row += 1
        if hint:
            ttk.Label(self.body, text=hint, foreground="#666").grid(
                row=self._row, column=1, sticky="w", padx=(10, 0))
            self._row += 1

    def buttons(self, ok_text="저장"):
        bar = ttk.Frame(self.body)
        bar.grid(row=self._row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="취소", command=self.top.destroy).pack(side="right")
        ttk.Button(bar, text=ok_text, command=self.submit).pack(side="right", padx=6)
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
                    row=self._row, column=1, sticky="w", padx=(10, 0))
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

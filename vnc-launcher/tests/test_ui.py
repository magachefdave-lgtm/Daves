"""화면(ui.py) 시험 - 실제 Tk 창을 띄우고 사용자처럼 누르고, 그려진 결과(캔버스 항목, 위젯 상태)를 확인한다.

밖으로 나가는 것은 모두 가짜로 바꾼다: 뷰어 실행(launch), 상태 확인(probe), 창 찾기(wintitle),
메시지 창·입력 창·파일 고르기. 설정 폴더와 설비 목록은 임시 폴더에 둔다.

리눅스에서는 화면이 필요하다:
    xvfb-run -a -s "-screen 0 1280x800x24" sh -c 'twm -f twmrc & python -m pytest -q tests'
(twm 은 창을 마우스로 놓기를 기다리므로 twmrc 에 RandomPlacement 를 넣는다. 창 관리자가 없으면
최소화 시험만 건너뛴다.) Windows 에서는 그냥 pytest 로 돈다. tkinter 가 없거나 화면이 없으면 건너뛴다.
REQUIRE_UI_TESTS=1 이면 건너뛰는 대신 실패한다 (CI 의 화면 시험 작업 - 시험이 조용히 빠지지 않게).
"""
from __future__ import annotations

import collections
import csv
import dataclasses
import json
import os
import threading
import time

import pytest

REQUIRED = os.environ.get("REQUIRE_UI_TESTS") == "1"
if REQUIRED:
    import tkinter as tk
else:
    tk = pytest.importorskip("tkinter")


def _skip(reason: str):
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


from launcher import APP_VERSION, core, secret, sign, ui, update  # noqa: E402
from launcher.core import Config, Equipment, EquipmentStore, save_config  # noqa: E402

REAL_REVEAL = ui.App._reveal
VIEWER = os.path.join("C:\\", "fake", "uvnc", "vncviewer.exe")


# ------------------------------------------------------------------ 가짜들 ---

class Dialogs:
    """messagebox / simpledialog / filedialog 대신. 부른 것을 기록하고 정해 둔 답을 준다."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.yes = collections.deque()       # askyesno 답 (비면 True)
        self.strings = collections.deque()   # askstring 답 (비면 None = 취소)
        self.files = collections.deque()     # 파일 고르기 답 (비면 "" = 취소)

    def _msg(self, kind):
        def show(*args, **kwargs):
            text = args[1] if len(args) > 1 else kwargs.get("message", "")
            self.calls.append((kind, str(text)))
            if kind == "askyesno":
                return self.yes.popleft() if self.yes else True
            return "ok"
        return show

    def askstring(self, title, prompt, **kwargs):
        self.calls.append(("askstring", str(prompt)))
        return self.strings.popleft() if self.strings else None

    def askfile(self, **kwargs):
        self.calls.append(("file", kwargs.get("title", "")))
        return self.files.popleft() if self.files else ""

    def texts(self, kind: str) -> list[str]:
        return [t for k, t in self.calls if k == kind]


class FakeProc:
    _pid = 50000

    def __init__(self, cmd):
        FakeProc._pid += 1
        self.pid, self.cmd, self.code = FakeProc._pid, cmd, None

    def poll(self):
        return self.code

    def exit(self, code=0):
        self.code = code


class Probe:
    """상태 확인 대신: 부른 것을 기록하고, down 에 든 주소만 응답 없음."""

    def __init__(self):
        self.calls: list[tuple[str, str, int]] = []
        self.down: set[str] = set()
        self.gate: threading.Event | None = None
        self._lock = threading.Lock()

    def __call__(self, method, host, port, timeout=1.5):
        with self._lock:
            self.calls.append((method, host, port))
        if self.gate is not None:
            self.gate.wait(5)
        return host not in self.down

    def methods(self) -> dict[str, str]:
        return {host: method for method, host, _p in self.calls}


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.appdir = tmp_path / "appdata"
        self.data = tmp_path / "shared"
        self.appdir.mkdir()
        self.data.mkdir()
        self.eqfile = self.data / "equipment.json"
        self.mp = monkeypatch
        self.dialogs = Dialogs()
        self.launched: list[FakeProc] = []
        self.probe = Probe()
        self.windows: dict[int, int] = {}      # pid -> 보이는 창 수 (wintitle.retitle 대신)
        self.focusable: set[int] = set()       # wintitle.focus 가 성공할 pid
        self.apps: list[ui.App] = []
        self._patch()

    def _patch(self):
        mp, d = self.mp, self.dialogs
        appdir = str(self.appdir)
        mp.setattr(core, "app_dir", lambda: appdir)
        mp.setattr(ui, "app_dir", lambda: appdir)
        mp.delenv("EQUIPMENT_REMOTE_EXE", raising=False)
        for kind in ("showinfo", "showwarning", "showerror", "askyesno"):
            mp.setattr(ui.messagebox, kind, d._msg(kind))
        mp.setattr(ui.simpledialog, "askstring", d.askstring)
        for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
            mp.setattr(ui.filedialog, name, d.askfile)
        mp.setattr(ui, "launch", self._launch)
        mp.setattr(ui, "probe", self.probe)
        mp.setattr(ui, "detect_viewer", lambda *a, **k: ("ultravnc", VIEWER))
        mp.setattr(ui.wintitle, "retitle", lambda pid, name, rename=True: self.windows.get(pid, 0))
        mp.setattr(ui.wintitle, "focus", lambda pid: pid in self.focusable)
        mp.setattr(ui.wintitle, "virtual_screen", lambda: None)
        # 처음 0.7초의 나타나는 효과는 건너뛴다 (그 자체는 test_reveal_animation_finishes 에서 본다)
        mp.setattr(ui.App, "_reveal", lambda self, t0=None: (setattr(self, "reveal", 2.0), self.draw()))

    def _launch(self, cmd):
        p = FakeProc(list(cmd))
        self.launched.append(p)
        return p

    def write_list(self, items: list[Equipment], groups: list[str] | None = None, lock: dict | None = None):
        data = {"version": 1, "groups": groups or [], "equipment": [dataclasses.asdict(e) for e in items]}
        if lock:
            data["lock"] = lock
        self.eqfile.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def write_config(self, **over):
        cfg = Config(viewer_type="ultravnc", viewer_path=VIEWER, equipment_file=str(self.eqfile),
                     check_on_start=False, auto_check_minutes=0, show_rdp=True, update_folder="")
        for k, v in over.items():
            setattr(cfg, k, v)
        save_config(cfg, str(self.appdir / "config.ini"))

    def start(self) -> ui.App:
        try:
            root = tk.Tk()
        except tk.TclError as e:   # 화면이 없다 (DISPLAY 없음 등)
            _skip(f"Tk 창을 열 수 없습니다: {e}")
        app = ui.App(root)
        self.apps.append(app)
        app.reveal = 2.0
        root.update()
        app.draw()
        root.update()
        return app

    def close_all(self):
        for app in self.apps:
            try:
                if app.root.winfo_exists():
                    for job in app.root.tk.splitlist(app.root.tk.call("after", "info")):
                        app.root.after_cancel(job)   # 닫은 뒤 남은 예약이 "invalid command" 를 찍지 않게
                    app.close()
            except tk.TclError:
                pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    e.write_config()
    yield e
    e.close_all()


# ---------------------------------------------------------------- 도우미 ---

def pump(app, cond=None, timeout=3.0) -> bool:
    """화면 이벤트를 돌린다. cond 가 있으면 참이 될 때까지 (최대 timeout 초)."""
    end = time.monotonic() + timeout
    while True:
        app.root.update()
        if cond is None or cond():
            return True
        if time.monotonic() > end:
            return False
        time.sleep(0.01)


def eq_list(n=50, groups=5) -> list[Equipment]:
    out = []
    for i in range(1, n + 1):
        out.append(Equipment(name=f"EQ-{i}", host=f"10.0.{i // 250}.{i % 250 + 1}",
                             group=f"{(i - 1) % groups + 1}라인", os="Linux" if i % 3 == 0 else "Windows"))
    return out


def by_name(app, name) -> Equipment:
    return next(e for e in app.items if e.name == name)


def cards(app) -> list[str]:
    """화면에 그려진 설비 버튼의 key (즐겨찾기 줄은 "q"+id)."""
    keys = []
    for item in app.canvas.find_all():
        for tag in app.canvas.gettags(item):
            if tag.startswith("card:"):
                keys.append(tag[5:])
    return keys


def card_fill(app, key) -> str:
    return app.canvas.itemcget(app.canvas.find_withtag(f"card:{key}")[0], "fill")


def _center(canvas, tag):
    x1, y1, x2, y2 = canvas.bbox(tag)
    return (x1 + x2) // 2, (y1 + y2) // 2


def click(app, key, zone=None, state=0):
    """설비 버튼(zone=None) 또는 그 안의 아이콘(zone="share"/"rdp")을 마우스로 누른다."""
    c = app.canvas
    x, y = _center(c, f"btn:{key}:{zone}" if zone else f"card:{key}")
    c.event_generate("<Motion>", x=x, y=y)
    c.event_generate("<ButtonPress-1>", x=x, y=y, state=state)
    c.event_generate("<ButtonRelease-1>", x=x, y=y, state=state)
    app.root.update()


def click_chip(app, group):
    c = app.chips
    x, y = _center(c, f"chip:{group}")
    c.event_generate("<Motion>", x=x, y=y)
    c.event_generate("<Button-1>", x=x, y=y)
    app.root.update()


def key_press(app, widget, keysym):
    """키보드 이벤트는 포커스가 있는 창으로 간다 - 포커스를 받을 수 없는 환경(일부 CI)이면 건너뛴다."""
    widget.focus_force()
    app.root.update()
    if app.root.focus_get() is not widget:
        _skip("키보드 포커스를 받을 수 없는 환경입니다")
    widget.event_generate(f"<{keysym}>")
    app.root.update()


def foot(app) -> str:
    return app.foot_var.get()


def history_rows(env, app) -> list[list[str]]:
    app.log_pool.submit(lambda: None).result(5)   # 일꾼이 쓰던 줄을 마저 쓰게
    path = app.log.path()
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))[1:]


def wait_checked(app, timeout=3.0) -> bool:
    return pump(app, lambda: app.pending == 0 and app.check_btn.enabled, timeout)


def plain_passwords(app, monkeypatch):
    """비밀번호를 그대로 저장하는 보관소 (DPAPI 없이 모든 OS 에서)."""
    monkeypatch.setattr(secret, "AVAILABLE", True)
    store = secret.PasswordStore(os.path.join(core.app_dir(), "passwords.json"),
                                 encrypt=lambda b: b, decrypt=lambda b: b)
    app.passwords = store
    return store


def menu_labels(menu) -> list[str]:
    end = menu.index("end")
    if end is None:
        return []
    out = []
    for i in range(end + 1):
        try:
            out.append(menu.entrycget(i, "label"))
        except tk.TclError:   # 구분선
            out.append("---")
    return out


def menu_invoke(menu, text):
    """글자가 text 인 항목을 누른다 (앞의 ✓ 표시와 빈칸은 무시)."""
    labels = menu_labels(menu)
    for i, label in enumerate(labels):
        if label.replace("✓", "").strip() == text:
            menu.invoke(i)
            return
    raise AssertionError(f"메뉴에 '{text}' 가 없습니다: {menu_labels(menu)}")


# ------------------------------------------------------------- 화면 배치 ---

def test_fifty_items_fit_without_scrolling(env):
    env.write_list(eq_list(50), [f"{i}라인" for i in range(1, 6)])
    app = env.start()
    if app.root.winfo_width() < 1180 or app.root.winfo_height() < 720:
        _skip("화면이 1180x720 보다 작습니다")
    keys = cards(app)
    assert len(keys) == 50
    assert not app.scrollable and not app.scroll.winfo_manager()
    height = app.canvas.winfo_height()
    for k in keys:
        _x1, y1, _x2, y2 = app.canvas.bbox(f"card:{k}")
        assert 0 <= y1 and y2 <= height, (k, y2, height)
    texts = {app.canvas.itemcget(i, "text") for i in app.canvas.find_all() if app.canvas.type(i) == "text"}
    assert {"1라인", "5라인"} <= texts
    assert app.summary["text"].startswith("전체 50대")


def test_too_many_items_scrolls(env):
    env.write_list(eq_list(160, groups=8), [f"{i}라인" for i in range(1, 9)])
    app = env.start()
    assert app.scrollable and app.scroll.winfo_manager() == "pack"
    assert app.th == ui.TILE_H_COMPACT


def test_reveal_animation_finishes(env):
    env.write_list(eq_list(5))
    app = env.start()
    app.reveal = 0.0
    REAL_REVEAL(app)
    assert pump(app, lambda: app.reveal >= 1, timeout=3)
    assert card_fill(app, by_name(app, "EQ-1").id) == ui.TILE


def test_empty_list_shows_hint(env):
    env.write_list([])
    app = env.start()
    texts = [app.canvas.itemcget(i, "text") for i in app.canvas.find_all() if app.canvas.type(i) == "text"]
    assert "등록된 설비가 없습니다" in texts


# --------------------------------------------------------- 그룹, 검색 ---

def test_group_chips_filter_and_remember(env):
    env.write_list(eq_list(50), [f"{i}라인" for i in range(1, 6)])
    app = env.start()
    click_chip(app, "2라인")
    assert app.group == "2라인"
    assert len(cards(app)) == 10
    assert all(by_name(app, n).group == "2라인" for n in
               [e.name for e in app.items if e.id in cards(app)])
    with open(env.appdir / "ui_state.json", encoding="utf-8") as f:
        assert json.load(f)["group"] == "2라인"
    click_chip(app, ui.ALL_GROUPS)
    assert len(cards(app)) == 50


def test_search_filters_escape_clears_and_enter_connects(env):
    env.write_list(eq_list(50), [f"{i}라인" for i in range(1, 6)])
    app = env.start()
    app.search.insert(0, "eq-1")
    app.root.update()
    shown = {e.name for e in app.items if e.id in cards(app)}
    assert shown == {"EQ-1"} | {f"EQ-{i}" for i in range(10, 20)}
    key_press(app, app.search, "Escape")
    assert app.search_var.get() == "" and len(cards(app)) == 50
    app.search_var.set("EQ-1 1라인")        # 여러 낱말 = 모두 맞아야 (이름 + 그룹)
    app.root.update()
    assert [e.name for e in app.visible()] == ["EQ-1", "EQ-11", "EQ-16"]
    app.search_var.set("EQ-37")
    app.root.update()
    assert [e.name for e in app.visible()] == ["EQ-37"]
    key_press(app, app.search, "Return")
    assert len(env.launched) == 1 and by_name(app, "EQ-37").host + "::5900" in env.launched[0].cmd


def test_search_no_match_message(env):
    env.write_list(eq_list(5))
    app = env.start()
    app.search_var.set("없는설비")
    app.root.update()
    texts = [app.canvas.itemcget(i, "text") for i in app.canvas.find_all() if app.canvas.type(i) == "text"]
    assert "조건에 맞는 설비가 없습니다" in texts


# ------------------------------------------------------------------ 접속 ---

def test_click_connects_with_ultravnc_command_and_password(env, monkeypatch):
    env.write_list([Equipment("A-1", "10.1.1.1"), Equipment("A-2", "10.1.1.2", port=5901)])
    app = env.start()
    pw = plain_passwords(app, monkeypatch)
    a1, a2 = by_name(app, "A-1"), by_name(app, "A-2")
    pw.set(a1.id, "pa ss")                       # 빈칸이 있어도 인자 하나로
    click(app, a1.id)
    assert env.launched[-1].cmd == [VIEWER, "-connect", "10.1.1.1::5900", "-password", "pa ss", "-nostatus"]
    click(app, a2.id)
    assert env.launched[-1].cmd == [VIEWER, "-connect", "10.1.1.2::5901", "-nostatus"]
    assert "저장된 비밀번호가 없어" in foot(app)
    # 접속 시도 중: 연한 파랑 버튼, 위쪽 줄에 "접속 시도 중"
    assert card_fill(app, a1.id) == ui.TRYING_BG
    chip_texts = [app.chips.itemcget(i, "text") for i in app.chips.find_all() if app.chips.type(i) == "text"]
    assert any(t.startswith("접속 시도 중") for t in chip_texts)
    rows = history_rows(env, app)
    assert [r[3] for r in rows] == ["A-1", "A-2"] and rows[0][5] == "VNC 접속"


def test_caution_asks_and_no_cancels(env):
    env.write_list([Equipment("PM-1", "10.2.0.1", caution="PM 중")])
    app = env.start()
    key = by_name(app, "PM-1").id
    assert card_fill(app, key) == ui.CAUTION_BG
    env.dialogs.yes.append(False)
    click(app, key)
    assert env.launched == []
    assert "PM 중" in env.dialogs.texts("askyesno")[-1]
    env.dialogs.yes.append(True)
    click(app, key)
    assert len(env.launched) == 1


def test_session_connect_reuse_and_close(env):
    env.write_list([Equipment("S-1", "10.3.0.1")])
    app = env.start()
    key = by_name(app, "S-1").id
    click(app, key)
    proc = env.launched[0]
    env.windows[proc.pid] = 1                    # 뷰어 창이 떴다
    app._tick_sessions()
    assert app.sessions[0]["connected"]
    assert card_fill(app, key) == ui.ACCENT
    chip_texts = [app.chips.itemcget(i, "text") for i in app.chips.find_all() if app.chips.type(i) == "text"]
    assert any(t.startswith("접속 중") and "S-1" in t for t in chip_texts)
    # 다시 누르면 새로 띄우지 않고 그 창을 앞으로
    env.focusable.add(proc.pid)
    click(app, key)
    assert len(env.launched) == 1 and "앞으로 가져왔습니다" in foot(app)
    # 창을 찾지 못하면 묻는다 - 아니오 = 새로 접속하지 않음
    env.focusable.clear()
    env.dialogs.yes.append(False)
    click(app, key)
    assert len(env.launched) == 1
    proc.exit(0)
    app._tick_sessions()
    assert app.sessions == [] and card_fill(app, key) == ui.TILE
    actions = [r[5] for r in history_rows(env, app)]
    assert actions == ["VNC 접속", "VNC 종료"]


def test_session_fails_before_window(env):
    env.write_list([Equipment("F-1", "10.3.0.9")])
    app = env.start()
    click(app, by_name(app, "F-1").id)
    env.launched[0].exit(1)                      # 창이 뜨기 전에 꺼짐 (접속 실패)
    app._tick_sessions()
    assert "접속하지 못했습니다" in foot(app)
    rows = history_rows(env, app)
    assert [r[5] for r in rows] == ["VNC 접속", "VNC 실패"] and rows[1][6] == "창이 뜨기 전에 닫힘"


def test_trying_click_does_not_launch_twice(env):
    env.write_list([Equipment("T-1", "10.3.1.1")])
    app = env.start()
    key = by_name(app, "T-1").id
    click(app, key)
    click(app, key)
    assert len(env.launched) == 1 and "접속 시도 중입니다" in foot(app)


# ------------------------------------------------------------- 상태 확인 ---

def _mixed():
    return [Equipment("LX", "10.9.0.1", os="Linux"), Equipment("WIN", "10.9.0.2"),
            Equipment("OFF", "10.9.0.3", check="off"), Equipment("LXP", "10.9.0.4", os="Linux", check="port"),
            Equipment("ETC", "10.9.0.5", os="기타")]


def test_check_uses_ping_for_linux_port_for_windows_and_skips_off(env):
    env.write_list(_mixed())
    app = env.start()
    env.probe.down = {"10.9.0.2"}
    app.check_status()
    assert not app.check_btn.enabled               # 확인 중에는 버튼이 잠긴다
    assert wait_checked(app)
    assert env.probe.methods() == {"10.9.0.1": "ping", "10.9.0.2": "port", "10.9.0.4": "port",
                                   "10.9.0.5": "port"}
    assert by_name(app, "OFF").id not in app.status
    win = by_name(app, "WIN").id
    assert app.status[win] is False and card_fill(app, win) == ui.ALERT_BG
    assert card_fill(app, by_name(app, "LX").id) == ui.TILE
    assert app.alert_btn.winfo_manager() == "pack" and app.alert_btn.text == "응답 없음  1대"
    assert "확인" in app.summary["text"]
    # 빨간 표시를 누르면 응답 없는 설비만
    app.toggle_only_down()
    assert cards(app) == [win]
    app.toggle_only_down()
    assert len(cards(app)) == 5


def test_check_does_not_probe_the_same_equipment_twice(env):
    env.write_list([Equipment("A", "10.8.0.1"), Equipment("B", "10.8.0.2", os="Linux")])
    app = env.start()
    env.probe.gate = threading.Event()
    app.check_status()
    app.check_status()                              # F5 를 연타해도
    key_press(app, app.search, "F5")
    time.sleep(0.1)
    assert len(env.probe.calls) == 2
    env.probe.gate.set()
    assert wait_checked(app)
    assert len(env.probe.calls) == 2 and app.inflight == set()
    app.check_status()                              # 끝난 뒤에는 다시 확인한다
    assert wait_checked(app)
    assert len(env.probe.calls) == 4


def test_newly_down_toast(env):
    env.write_list([Equipment("UP", "10.7.0.1"), Equipment("GOES-DOWN", "10.7.0.2")])
    app = env.start()
    app.check_status()
    assert wait_checked(app)
    env.probe.down = {"10.7.0.2"}
    app.check_status(quiet=True)
    assert wait_checked(app)
    assert pump(app, lambda: "GOES-DOWN" in foot(app))
    assert "응답 없음으로 바뀐 설비" in foot(app)


def test_auto_check_pauses_while_minimized(env):
    env.write_config(auto_check_minutes=1)
    env.write_list([Equipment("A", "10.6.0.1")])
    app = env.start()
    app.root.iconify()
    real = pump(app, lambda: app.root.state() == "iconic", timeout=5)
    if not real:
        # 창 관리자가 늦게 뜨거나 없어 실제로 내려가지 않았다 - 프로그램이 보는 상태만 바꿔 같은 흐름을 본다
        env.mp.setattr(app.root, "state", lambda: "iconic")
    app.last_check = time.monotonic() - 120
    app._tick_clock()
    pump(app)
    assert env.probe.calls == []                    # 내려 둔 동안은 두드리지 않는다
    if real:
        app.root.deiconify()
    else:
        env.mp.setattr(app.root, "state", lambda: "normal")
        app._on_map(type("E", (), {"widget": app.root})())
    assert pump(app, lambda: env.probe.calls, timeout=3)   # 다시 열면 바로 확인
    assert wait_checked(app)


def test_auto_check_runs_when_due(env):
    env.write_config(auto_check_minutes=1)
    env.write_list([Equipment("A", "10.6.1.1")])
    app = env.start()
    app.last_check = time.monotonic() - 30
    app._tick_clock()
    pump(app)
    assert env.probe.calls == []                    # 아직 1분이 안 됐다
    app.last_check = time.monotonic() - 61
    app._tick_clock()
    assert wait_checked(app) and len(env.probe.calls) == 1


# ------------------------------------------------------------- 편집 잠금 ---

def _spy(monkeypatch, name):
    made = []
    base = getattr(ui, name)

    class Spy(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(ui, name, Spy)
    return made


def test_edit_lock_pin_flow(env, monkeypatch):
    env.write_list([Equipment("L-1", "10.5.0.1")])
    app = env.start()
    locks = _spy(monkeypatch, "LockDialog")
    edits = _spy(monkeypatch, "EquipmentDialog")
    app.open_lock()
    dlg = locks[-1]
    dlg.new.set("12")
    dlg.again.set("12")
    dlg.submit()                                     # 너무 짧다
    assert "이상이어야" in env.dialogs.texts("showwarning")[-1] and dlg.top.winfo_exists()
    dlg.new.set("4321")
    dlg.again.set("4321")
    dlg.submit()
    with open(env.eqfile, encoding="utf-8") as f:
        assert "lock" in json.load(f)
    assert app.store.lock and not app.unlocked_at

    eq = by_name(app, "L-1")
    env.dialogs.strings.append("0000")
    app.edit(eq)
    assert edits == [] and "PIN이 맞지 않습니다" in env.dialogs.texts("showwarning")[-1]
    env.dialogs.strings.append("4321")
    app.edit(eq)
    assert len(edits) == 1 and app.unlocked_at
    assert "편집 잠금 풀림" in app.summary["text"]
    edits[0].name.set("L-1 수정")
    edits[0].submit()
    assert [e.name for e in app.items] == ["L-1 수정"]
    # 잠금을 푼 동안에는 다시 묻지 않는다
    asked = len(env.dialogs.texts("askstring"))
    app.edit(by_name(app, "L-1 수정"))
    assert len(env.dialogs.texts("askstring")) == asked and len(edits) == 2
    edits[1].top.destroy()
    app.relock()
    assert not app.unlocked_at and "편집 잠금 풀림" not in app.summary["text"]
    # 오래 두면 저절로 다시 잠긴다
    env.dialogs.strings.append("4321")
    assert app._can_edit()
    app.unlocked_at = time.monotonic() - ui.RELOCK_SECONDS - 1
    app._tick_clock()
    assert not app.unlocked_at
    # 다른 PC 에서도 같은 잠금 (목록 파일에 있으므로)
    other = EquipmentStore(str(env.eqfile))
    other.load()
    assert other.lock == app.store.lock


def test_locked_delete_cancelled_without_pin(env):
    env.write_list([Equipment("D-1", "10.5.1.1")], lock=core.make_lock("9999"))
    app = env.start()
    app.delete([by_name(app, "D-1").id])            # PIN 창에서 취소
    assert [e.name for e in app.items] == ["D-1"]
    assert env.dialogs.texts("askyesno") == []


# ----------------------------------------------------------- 여러 대 선택 ---

def test_selection_range_bulk_caution_and_check_method(env):
    items = [Equipment(f"R-{i}", f"10.4.0.{i}", group="G", os="Linux" if i % 2 else "Windows")
             for i in range(1, 9)]
    env.write_list(items, ["G"])
    app = env.start()
    ids = {e.name: e.id for e in app.items}
    click(app, ids["R-2"], state=0x4)                # Ctrl+클릭: 선택 시작
    assert app.selecting and app.selbar.winfo_manager() == "pack"
    click(app, ids["R-6"], state=0x1)                # Shift+클릭: 범위
    chosen = {ids[f"R-{i}"] for i in range(2, 7)}
    assert app.selected == chosen
    assert app.sel_label["text"] == "5대 선택"
    assert all(app.canvas.itemcget(app.canvas.find_withtag(f"card:{k}")[0], "width") == "2.0"
               for k in chosen)
    click(app, ids["R-4"])                           # 선택 중 클릭 = 빼기
    chosen.discard(ids["R-4"])
    assert app.selected == chosen
    assert env.launched == []                        # 선택 중에는 접속하지 않는다

    picked = sorted(app.selected)
    app._fill_caution_menu(app.sub_caution, picked)
    menu_invoke(app.sub_caution, "PM 중")
    assert {by_name(app, n).caution for n in ("R-2", "R-3", "R-5", "R-6")} == {"PM 중"}
    assert by_name(app, "R-4").caution == ""
    assert card_fill(app, ids["R-3"]) != ui.TILE

    app._fill_check_menu(app.sub_check, picked)
    env.dialogs.yes.append(False)                    # 리눅스를 포트로? -> 아니오
    menu_invoke(app.sub_check, "VNC 포트")
    assert "Too many security failures" in env.dialogs.texts("askyesno")[-1]
    assert {by_name(app, n).check for n in ("R-2", "R-3")} == {"auto"}
    env.dialogs.yes.append(True)
    menu_invoke(app.sub_check, "VNC 포트")
    assert {by_name(app, n).check for n in ("R-2", "R-3", "R-5", "R-6")} == {"port"}
    assert wait_checked(app)
    assert env.probe.methods()["10.4.0.3"] == "port"

    key_press(app, app.search, "Escape")             # Esc = 선택 끝내기
    assert not app.selecting and app.selbar.winfo_manager() == ""


def test_select_all_visible_and_bulk_move(env):
    env.write_list(eq_list(12, groups=3), ["1라인", "2라인", "3라인"])
    app = env.start()
    click_chip(app, "2라인")
    app.start_selection()
    app.select_all_visible()
    assert len(app.selected) == 4
    app._fill_move_menu(app.sub_move, sorted(app.selected))
    menu_invoke(app.sub_move, "3라인")
    assert sum(1 for e in app.items if e.group == "3라인") == 8


def test_delete_removes_favorite_and_password(env, monkeypatch):
    env.write_list([Equipment("X-1", "10.3.3.1"), Equipment("X-2", "10.3.3.2")])
    app = env.start()
    pw = plain_passwords(app, monkeypatch)
    x1 = by_name(app, "X-1").id
    pw.set(x1, "secret")
    app.set_favorite([x1], True)
    assert ui.QUICK + x1 in cards(app)               # 맨 위 즐겨찾기 줄
    env.dialogs.yes.append(True)
    app.delete([x1])
    assert [e.name for e in app.items] == ["X-2"]
    assert not pw.has(x1) and x1 not in app.local.favorites
    assert ui.QUICK + x1 not in cards(app) and x1 not in cards(app)
    with open(env.appdir / "ui_state.json", encoding="utf-8") as f:
        assert x1 not in json.load(f)["favorites"]
    assert (env.data / "backup").is_dir()            # 그날 처음 상태가 남는다


def test_delete_key_in_selection(env):
    env.write_list([Equipment("K-1", "10.3.4.1"), Equipment("K-2", "10.3.4.2")])
    app = env.start()
    app.start_selection(by_name(app, "K-1").id)
    env.dialogs.yes.append(True)
    key_press(app, app.search, "Delete")             # 검색 칸이 비어 있으면 고른 설비 삭제
    assert [e.name for e in app.items] == ["K-2"]


# ------------------------------------------------------------- 가져오기 ---

def test_csv_import_with_passwords(env, monkeypatch):
    env.write_list([Equipment("OLD", "10.2.2.1", caution="고장")])
    app = env.start()
    pw = plain_passwords(app, monkeypatch)
    path = env.tmp / "import.csv"
    path.write_text("설비명,그룹,IP,OS,주의,비밀번호\n"
                    "NEW-1,1라인,192.168.010.011,Linux,,pw1\n"
                    "OLD,1라인,10.2.2.9,Windows,PM 중,pw2\n"
                    "BAD,1라인,999.1.1.1,Windows,,\n", encoding="utf-8-sig")
    env.dialogs.files.append(str(path))
    app.import_csv()
    new, old = by_name(app, "NEW-1"), by_name(app, "OLD")
    assert new.host == "192.168.10.11" and new.os == "Linux"
    assert old.host == "10.2.2.9" and old.caution == "고장"      # 주의 표시는 CSV 로 덮지 않는다
    assert pw.get(new.id) == "pw1" and pw.get(old.id) == "pw2"
    msg = env.dialogs.texts("showwarning")[-1]
    assert "설비 추가 1대, 수정 1대" in msg and "비밀번호 저장 2대" in msg and "BAD" in msg
    assert wait_checked(app)
    assert env.probe.methods()["192.168.10.11"] == "ping"


def test_export_csv(env):
    env.write_list([Equipment("E-1", "10.2.3.1", group="G")], ["G"])
    app = env.start()
    out = env.tmp / "out.csv"
    env.dialogs.files.append(str(out))
    app.export_csv()
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0][0] == "설비명" and rows[1][:3] == ["E-1", "G", "10.2.3.1"]


# --------------------------------------------------------- 공유 목록 ---

def test_follows_changes_from_another_pc(env):
    env.write_list([Equipment("S-1", "10.1.0.1")])
    app = env.start()
    other = EquipmentStore(str(env.eqfile))         # 다른 PC
    other.add(Equipment("S-2", "10.1.0.2"))
    t = time.time() + 5
    os.utime(env.eqfile, (t, t))
    app._watch_file()
    assert {e.name for e in app.items} == {"S-1", "S-2"}
    assert by_name(app, "S-2").id in cards(app)


def test_missing_shared_file_keeps_list_and_never_saves_empty(env):
    env.write_list([Equipment("N-1", "10.1.1.1"), Equipment("N-2", "10.1.1.2")])
    app = env.start()
    os.remove(env.eqfile)                            # 네트워크가 끊겨 파일이 안 보인다
    app._watch_file()
    assert len(app.items) == 2 and len(cards(app)) == 2
    assert "연결할 수 없습니다" in foot(app)
    app.set_caution([app.items[0].id], "PM 중")      # 저장을 시도해도
    assert not env.eqfile.exists()                   # 빈 목록으로 새로 만들지 않는다
    assert env.dialogs.calls                         # 사용자에게 알렸다


def test_edit_keeps_caution_set_by_another_pc(env, monkeypatch):
    env.write_list([Equipment("M-1", "10.1.2.1")])
    app = env.start()
    edits = _spy(monkeypatch, "EquipmentDialog")
    app.edit(by_name(app, "M-1"))
    other = EquipmentStore(str(env.eqfile))
    other.set_fields([app.items[0].id], caution="PM 중")   # 수정 창을 연 사이 다른 PC 가
    edits[0].memo.set("메모")
    edits[0].submit()
    e = by_name(app, "M-1")
    assert e.memo == "메모" and e.caution == "PM 중"


# ------------------------------------------------------------- 창, 메뉴 ---

def test_window_geometry_saved_and_restored(env):
    env.write_list([Equipment("W-1", "10.0.0.1")])
    app = env.start()
    app.root.geometry("900x600+40+30")
    pump(app, lambda: app.root.winfo_width() == 900, timeout=2)
    pump(app)
    app.close()
    with open(env.appdir / "ui_state.json", encoding="utf-8") as f:
        assert json.load(f)["geometry"].startswith("900x600")
    app2 = env.start()
    assert app2.root.geometry().startswith("900x600")


def _more_menu(app, monkeypatch, shift=False):
    monkeypatch.setattr(app.more_menu, "tk_popup", lambda *a, **k: None)
    b = app.more_btn
    b.event_generate("<ButtonPress-1>", x=5, y=5)
    b.event_generate("<ButtonRelease-1>", x=5, y=5, state=0x1 if shift else 0)
    app.root.update()
    return menu_labels(app.more_menu)


def test_admin_menu_hidden_on_pack_installed_pc(env, monkeypatch):
    env.write_list([])
    app = env.start()
    assert "배포본 만들기…" in _more_menu(app, monkeypatch)           # 관리자 PC (배포본으로 설치 안 함)
    app.local.applied_pack = "pack-id"                                  # 배포본으로 설치한 PC
    labels = _more_menu(app, monkeypatch)
    assert "배포본 만들기…" not in labels and "업데이트 올리기…" not in labels
    labels = _more_menu(app, monkeypatch, shift=True)                   # Shift+[더보기]
    assert "배포본 만들기…" in labels and "편집 잠금 설정…" in labels
    assert "배포본 만들기…" not in _more_menu(app, monkeypatch)        # 한 번만


def test_right_click_menu_entries(env):
    env.write_list([Equipment("RC-1", "10.0.1.1"), Equipment("RC-2", "10.0.1.2", os="Linux")])
    app = env.start()
    win = menu_labels(app._single_menu(by_name(app, "RC-1")))
    assert "공유폴더 열기" in win and "원격 데스크톱(RDP) 접속" in win and "IP 복사" in win
    lx = menu_labels(app._single_menu(by_name(app, "RC-2")))
    assert "공유폴더 열기" not in lx and not any("원격 데스크톱" in t for t in lx)
    menu_invoke(app._single_menu(by_name(app, "RC-1")), "IP 복사")
    assert app.root.clipboard_get() == "10.0.1.1"


def test_rdp_warns_once(env, monkeypatch):
    monkeypatch.setattr(ui.sys, "platform", "win32")
    env.write_list([Equipment("RD-1", "10.0.2.1")])
    app = env.start()
    key = by_name(app, "RD-1").id
    click(app, key, zone="rdp")
    assert env.launched[-1].cmd == ["mstsc.exe", "/v:10.0.2.1"]
    assert len(env.dialogs.texts("askyesno")) == 1
    env.launched[-1].exit(0)
    app._tick_sessions()
    click(app, key, zone="rdp")
    assert len(env.launched) == 2 and len(env.dialogs.texts("askyesno")) == 1


# ------------------------------------------------------------- 업데이트 ---

def _upload(env, version, seed=None):
    exe = env.tmp / "build.exe"
    exe.write_bytes(b"MZ fake exe " + version.encode())
    folder = env.tmp / "update"
    return update.publish(str(exe), str(folder), version, "notes", signing_seed=seed), str(folder)


def test_update_button_appears_for_newer_and_hides_for_same(env):
    env.write_list([])
    app = env.start()
    seed = bytes(range(32))
    _info, folder = _upload(env, "99.0.0", seed)
    app.cfg.update_folder = folder
    app.check_update(manual=False)
    assert pump(app, lambda: app.update_btn.winfo_manager() == "pack")
    assert "99.0.0" in app.update_btn.text
    _upload(env, APP_VERSION, seed)
    app.check_update(manual=False)
    assert pump(app, lambda: app.update_btn.winfo_manager() == "")


def test_unsigned_update_is_never_installed(env):
    # 믿는 키가 아직 없는 PC 라도 서명 없는 새 버전은 받지 않는다 (1.4.7 부터 관리자는 늘 서명한다)
    env.write_list([])
    app = env.start()
    _info, folder = _upload(env, "99.0.0")
    app.cfg.update_folder = folder
    app.check_update(manual=True)
    assert pump(app, lambda: env.dialogs.texts("showwarning"))
    assert "서명이 없습니다" in env.dialogs.texts("showwarning")[-1]
    assert app.update_info is None and app.update_btn.winfo_manager() == "" and app.local.update_key == ""


def test_second_check_while_one_runs_is_merged(env, monkeypatch):
    env.write_list([])
    app = env.start()
    _info, folder = _upload(env, "99.0.0", bytes(range(32)))
    app.cfg.update_folder = folder
    gate = threading.Event()
    reads = []
    real = ui.read_latest

    def slow(f):
        reads.append(f)
        gate.wait(5)
        return real(f)

    monkeypatch.setattr(ui, "read_latest", slow)
    monkeypatch.setattr(app, "start_update", lambda: env.dialogs.calls.append(("start_update", "")))
    app.check_update(manual=False)
    app.check_update(manual=True)          # 먼저 것이 돌고 있다 - 새로 읽지 않고, 결과는 직접 누른 것처럼
    gate.set()
    assert pump(app, lambda: not app.update_checking)
    assert len(reads) == 1 and env.dialogs.texts("start_update") == [""]


def test_check_result_during_install_leaves_button_alone(env):
    env.write_list([])
    app = env.start()
    _info, folder = _upload(env, "99.0.0", bytes(range(32)))
    app.cfg.update_folder = folder
    app.check_update(manual=False)
    assert pump(app, lambda: app.update_info is not None)
    app.updating = True
    app.update_btn.set_enabled(False, "받는 중…")
    app._update_checked((app.update_info, ("ok", app.local.update_key)), None, False)
    assert not app.update_btn.enabled and "받는 중" in app.update_btn.text


def test_update_signed_key_is_pinned_and_other_key_rejected(env):
    env.write_list([])
    app = env.start()
    seed_a, seed_b = bytes(range(32)), bytes(range(1, 33))
    info, folder = _upload(env, "99.0.0", seed_a)
    app.cfg.update_folder = folder
    app.check_update(manual=False)
    assert pump(app, lambda: app.update_btn.winfo_manager() == "pack")
    assert app.local.update_key == sign.public_key(seed_a).hex()   # 처음 본 키를 기억
    _upload(env, "99.0.1", seed_b)                                   # 다른 키로 올라온 것
    app.check_update(manual=False)
    assert pump(app, lambda: app.update_btn.winfo_manager() == "")
    assert "서명" in foot(app)
    _upload(env, "99.0.2")                                           # 서명 없는 것도 이제는 거부
    app.check_update(manual=True)
    assert pump(app, lambda: env.dialogs.texts("showwarning"))
    assert app.update_info is None


def test_manual_update_check_without_folder(env):
    env.write_list([])
    app = env.start()
    app.check_update(manual=True)
    assert "업데이트 폴더가 정해지지 않았습니다" in env.dialogs.texts("showinfo")[-1]


def test_close_saves_state_and_stops(env):
    env.write_list([Equipment("C-1", "10.0.3.1")])
    app = env.start()
    app.set_favorite([app.items[0].id], True)
    root = app.root
    app.close()
    assert not _exists(root)
    with open(env.appdir / "ui_state.json", encoding="utf-8") as f:
        assert json.load(f)["favorites"] == [app.items[0].id]


def _exists(root) -> bool:
    try:
        return bool(root.winfo_exists())
    except tk.TclError:
        return False


# ---------------------------------------------- 1.4.7: 서명·받기·읽기 전용 ---

def _own_exe(env, monkeypatch) -> str:
    exe = env.tmp / "app" / "EquipmentRemote.exe"
    exe.parent.mkdir(exist_ok=True)
    exe.write_bytes(b"MZ running exe")
    monkeypatch.setenv("EQUIPMENT_REMOTE_EXE", str(exe))
    return str(exe)


def test_publish_creates_key_signs_and_backs_up(env, monkeypatch):
    env.write_list([])
    _own_exe(env, monkeypatch)
    folder = env.tmp / "update"
    folder.mkdir()
    app = env.start()
    app.cfg.update_folder = str(folder)
    backup = str(env.tmp / "key_backup.json")
    env.dialogs.yes.extend([True])                         # 백업 만들까요? -> 예
    env.dialogs.strings.extend(["backup-pw-1", "backup-pw-1", "첫 서명"])
    env.dialogs.files.append(backup)
    app.publish_update()
    assert pump(app, lambda: (folder / "latest.json").exists() and not app.updating, timeout=10)
    info = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
    store = update.SigningKeyStore(str(env.appdir / update.KEY_FILE),
                                   encrypt=lambda b: b, decrypt=lambda b: b)
    assert info["key"] == store.public_hex() == app.local.update_key
    assert update.verify_info(info, info["key"]) == ("ok", info["key"])
    assert os.path.exists(backup)
    assert "서명 키" in env.dialogs.texts("showinfo")[-1]
    # 다른 PC: 백업에서 가져오면 같은 키
    other = update.SigningKeyStore(str(env.tmp / "other.json"), encrypt=lambda b: b, decrypt=lambda b: b)
    assert other.import_file(backup, "backup-pw-1") == info["key"]


def test_publish_with_other_key_than_server_asks_first(env, monkeypatch):
    env.write_list([])
    _own_exe(env, monkeypatch)
    _info, folder = _upload(env, "1.0.0", bytes(range(32)))    # 서버는 다른 키로 서명돼 있다
    app = env.start()
    app.cfg.update_folder = folder
    env.dialogs.yes.extend([False])                             # 새 키로 올릴까요? -> 아니오
    app.publish_update()
    pump(app)
    assert "서명 키" in env.dialogs.texts("askyesno")[0]
    assert not os.path.exists(env.appdir / update.KEY_FILE)     # 키를 만들지도 않았다
    info = json.loads(open(os.path.join(folder, "latest.json"), encoding="utf-8").read())
    assert info["version"] == "1.0.0"


def test_update_downloads_in_background_then_relaunches(env, monkeypatch):
    env.write_list([])
    exe = _own_exe(env, monkeypatch)
    seed = bytes(range(32))
    _info, folder = _upload(env, "99.0.0", seed)
    relaunched = []
    monkeypatch.setattr(ui, "relaunch", lambda target, args=(): relaunched.append(target))
    gate = threading.Event()
    real_install = ui.install

    def slow_install(*a, **k):
        gate.wait(5)
        return real_install(*a, **k)

    monkeypatch.setattr(ui, "install", slow_install)
    app = env.start()
    root = app.root
    app.cfg.update_folder = folder
    app.check_update(manual=False)
    assert pump(app, lambda: app.update_info is not None)
    app.start_update()                                          # 확인 창 -> 예 (기본)
    root.update()
    assert app.updating and not app.update_btn.enabled and "받는 중" in app.update_btn.text
    assert app.root.winfo_exists()                              # 받는 동안에도 화면은 돈다
    gate.set()
    assert pump(app, lambda: relaunched or not _exists(root), timeout=10)
    assert relaunched == [exe] and not _exists(root)
    assert open(exe, "rb").read() == b"MZ fake exe 99.0.0"
    assert os.path.exists(exe + ".old")


def test_update_install_failure_restores_button(env, monkeypatch):
    env.write_list([])
    _own_exe(env, monkeypatch)
    _info, folder = _upload(env, "99.0.0", bytes(range(32)))
    app = env.start()
    app.cfg.update_folder = folder
    app.check_update(manual=False)
    assert pump(app, lambda: app.update_info is not None)
    with open(os.path.join(folder, "EquipmentRemote.exe"), "ab") as f:   # 받는 사이 파일이 바뀌었다
        f.write(b"tampered")
    app.start_update()
    assert pump(app, lambda: env.dialogs.texts("showerror"), timeout=10)
    assert "다릅니다" in env.dialogs.texts("showerror")[-1]
    assert not app.updating and app.update_btn.enabled and "99.0.0" in app.update_btn.text


def test_read_only_list_blocks_changes_but_not_connect(env, monkeypatch):
    env.write_list([Equipment("R-1", "10.0.9.1")])
    monkeypatch.setattr(EquipmentStore, "_probe_writable", lambda self: False)
    app = env.start()
    assert app.store.read_only and "읽기 전용" in app.summary["text"]
    app.add()
    app.set_caution([app.items[0].id], "PM 중")
    app.delete([app.items[0].id])
    texts = env.dialogs.texts("showinfo")
    assert len(texts) == 3 and all("읽기 전용" in t for t in texts)
    assert not env.dialogs.texts("askyesno")                  # 삭제 확인까지 가지 않는다
    assert [e.caution for e in EquipmentStore(str(env.eqfile)).load()] == [""]
    app.connect(app.items[0])                                  # 접속은 된다
    assert env.launched and "10.0.9.1::5900" in env.launched[-1].cmd
    monkeypatch.setattr(EquipmentStore, "_probe_writable", lambda self: True)   # 권한이 생기면 바로 풀린다
    app.set_caution([app.items[0].id], "PM 중")
    assert [e.caution for e in EquipmentStore(str(env.eqfile)).load()] == ["PM 중"]
    assert "읽기 전용" not in app.summary["text"]


def test_pack_pins_update_key(env):
    env.write_list([])
    app = env.start()
    key = sign.public_key(bytes(range(32))).hex()
    pack = {"id": "p1", "config": dataclasses.asdict(app.cfg), "equipment": None, "update_key": key}
    app._apply_pack(pack)
    assert app.local.update_key == key and app._trusted_key() == key
    assert "업데이트 서명 키" in env.dialogs.texts("showinfo")[-1]


def test_check_method_asks_pin_before_linux_warning(env):
    env.write_list([Equipment("L-1", "10.0.8.1", os="Linux")], lock=core.make_lock("1234"))
    app = env.start()
    app.set_check([app.items[0].id], "port")                   # PIN 창 -> 취소
    assert env.dialogs.texts("askstring") and not env.dialogs.texts("askyesno")
    assert EquipmentStore(str(env.eqfile)).load()[0].check == "auto"


def test_close_cancels_scheduled_jobs(env):
    env.write_list([])
    app = env.start()
    root = app.root
    app.close()
    assert not _exists(root)

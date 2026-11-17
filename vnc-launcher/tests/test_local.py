import csv
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher import history, local


def test_state_roundtrip_and_bad_files(tmp_path):
    p = str(tmp_path / "ui_state.json")
    s = local.LocalState(p)                     # 파일이 없으면 기본값
    assert (s.favorites, s.recent, s.geometry, s.zoomed, s.group) == ([], [], "", False, "")
    s.set_favorite(["a", "b", "a"], True)
    for i in "xyz":
        s.touch(i)
    s.touch("x")
    s.geometry, s.zoomed, s.group = "900x600+10+20", True, "식각"
    assert s.save()
    t = local.LocalState(p)
    assert (t.favorites, t.recent, t.geometry, t.zoomed, t.group) == (
        ["a", "b"], ["x", "z", "y"], "900x600+10+20", True, "식각")
    for bad in ("{broken", "[1, 2]", json.dumps({"favorites": "a", "recent": [1, None, "q"],
                                                 "zoomed": "yes", "geometry": 5})):
        open(p, "w", encoding="utf-8").write(bad)
        u = local.LocalState(p)
        assert u.favorites == [] and u.recent in ([], ["q"]) and u.zoomed is False and u.geometry == ""


def test_favorites_recent_forget(tmp_path):
    s = local.LocalState(str(tmp_path / "s.json"))
    assert s.set_favorite(["a", "b"], True) == 2 and s.set_favorite(["a"], True) == 0
    assert s.set_favorite(["a", "c"], False) == 1 and s.favorites == ["b"]
    for i in range(local.RECENT_MAX + 5):
        s.touch(f"e{i}")
    assert len(s.recent) == local.RECENT_MAX and s.recent[0] == f"e{local.RECENT_MAX + 4}"
    s.forget(["b", s.recent[0]])
    assert s.favorites == [] and s.recent[0] == f"e{local.RECENT_MAX + 3}"


def test_quick_ids_layout():
    existing = {"a", "b", "c", "d", "e", "f"}
    q = local.quick_ids
    assert q([], [], existing, 4) == ([], 0)
    assert q(["a"], ["b", "a", "c", "gone"], existing, 4) == (["a", "b", "c"], 1)   # 최근이 남은 칸을 채움
    assert q([], ["b", "c", "d", "e", "f"], existing, 3) == (["b", "c", "d"], 0)    # 최근만: 한 줄
    assert q(["a", "b", "c", "d"], ["e"], existing, 3) == (["a", "b", "c", "d", "e"], 4)  # 즐겨찾기는 모두
    assert q(["a", "b", "c"], ["e"], existing, 3) == (["a", "b", "c"], 3)           # 줄이 꽉 차면 최근 없음
    assert q(["gone"], [], existing, 3) == ([], 0)


def test_fit_geometry():
    fit = local.fit_geometry
    screen = (0, 0, 1920, 1080)
    assert fit("1180x720+100+50", screen, (760, 420)) == "1180x720+100+50"
    assert fit("1180x720+3000+50", screen, (760, 420)) == "1180x720+740+50"     # 뺀 모니터에 있던 창
    assert fit("1180x720+-8+-8", screen, (760, 420)) == "1180x720+0+0"
    assert fit("3000x2000+0+0", screen, (760, 420)) == "1920x1080+0+0"
    assert fit("100x100+0+0", screen, (760, 420)) == "760x420+0+0"
    assert fit("1000x700+-1900+100", (-1920, 0, 3840, 1080), (760, 420)) == "1000x700+-1900+100"  # 왼쪽 모니터
    assert fit("", screen, (760, 420)) is None and fit("zoomed", screen, (760, 420)) is None


def test_connection_log(tmp_path):
    clock = [datetime.datetime(2026, 11, 30, 23, 59, 1)]
    log = history.ConnectionLog(str(tmp_path / "share"), now=lambda: clock[0])
    assert log.view_copy() is None
    assert log.write("VNC 접속", "ETCH-01", "10.0.0.1")
    assert log.write("VNC 종료", "ETCH-01", "10.0.0.1", "12분")
    clock[0] = datetime.datetime(2026, 12, 1, 0, 0, 5)
    assert log.write("공유폴더 열기", "CVD-01", "10.0.0.2", r"\\10.0.0.2\D$")
    nov = tmp_path / "share" / "접속이력_2026-11.csv"
    raw = nov.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\xef\xbb\xbf") == 1   # BOM 은 처음 한 번
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert rows[0] == list(history.COLUMNS) and len(rows) == 3
    assert rows[1][3:6] == ["ETCH-01", "10.0.0.1", "VNC 접속"] and rows[2][6] == "12분"
    dec = tmp_path / "share" / "접속이력_2026-12.csv"
    assert dec.exists() and log.path().endswith("2026-12.csv")
    copy = log.view_copy()
    assert copy != str(dec) and open(copy, "rb").read() == dec.read_bytes()   # 원본이 아닌 복사본


def test_connection_log_failure_is_swallowed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = history.ConnectionLog(str(blocker / "sub"))      # 폴더를 만들 수 없는 경로
    assert log.write("VNC 접속", "A", "1.1.1.1") is False


def test_two_windows_on_one_pc_do_not_drop_favorites(tmp_path):
    p = str(tmp_path / "ui_state.json")
    w1, w2 = local.LocalState(p), local.LocalState(p)   # 같은 PC에 창 두 개
    w1.set_favorite(["eq3"], True)
    w1.save()
    w2.touch("eq9")                                    # 오래된 목록을 들고 있던 창이 저장
    w2.save()
    w3 = local.LocalState(p)
    assert w3.favorites == ["eq3"] and w3.recent == ["eq9"]
    w4 = local.LocalState(p)
    w1.set_favorite(["eq5"], True)                     # 두 창이 번갈아 즐겨찾기를 바꿔도
    w1.save()
    w4.set_favorite(["eq7"], True)
    w4.save()
    assert local.LocalState(p).favorites == ["eq3", "eq5", "eq7"]


def test_history_view_copies_do_not_collide(tmp_path):
    clock = [datetime.datetime(2026, 11, 5, 9, 0, 0)]
    log = history.ConnectionLog(str(tmp_path), now=lambda: clock[0])
    log.write("VNC 접속", "A", "1.1.1.1")
    first = log.view_copy()
    clock[0] = datetime.datetime(2026, 11, 5, 9, 0, 7)
    second = log.view_copy()
    assert first != second and os.path.exists(first) and os.path.exists(second)
    for f in (first, second):
        os.remove(f)

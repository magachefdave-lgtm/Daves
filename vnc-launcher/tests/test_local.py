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
    assert (s.favorites, s.geometry, s.zoomed, s.group) == ([], "", False, "")
    s.set_favorite(["a", "b", "a"], True)
    s.geometry, s.zoomed, s.group = "900x600+10+20", True, "식각"
    assert s.save()
    t = local.LocalState(p)
    assert (t.favorites, t.geometry, t.zoomed, t.group) == (["a", "b"], "900x600+10+20", True, "식각")
    assert "recent" not in open(p, encoding="utf-8").read()        # 최근 접속은 더 남기지 않는다
    for bad in ("{broken", "[1, 2]", json.dumps({"favorites": "a", "recent": ["x"],
                                                 "zoomed": "yes", "geometry": 5})):
        open(p, "w", encoding="utf-8").write(bad)
        u = local.LocalState(p)
        assert u.favorites == [] and u.zoomed is False and u.geometry == ""


def test_favorites_and_forget(tmp_path):
    s = local.LocalState(str(tmp_path / "s.json"))
    assert s.set_favorite(["a", "b"], True) == 2 and s.set_favorite(["a"], True) == 0
    assert s.set_favorite(["a", "c"], False) == 1 and s.favorites == ["b"]
    s.forget(["b"])
    assert s.favorites == []


def test_quick_ids_is_favorites_only():
    existing = {"a", "b", "c"}
    assert local.quick_ids([], existing) == []
    assert local.quick_ids(["c", "gone", "a"], existing) == ["c", "a"]    # 넣은 순서, 없는 설비는 뺌


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
    w2.refresh_lists()                                 # 오래된 목록을 들고 있던 창이 저장 (닫을 때와 같이)
    w2.save()
    w3 = local.LocalState(p)
    assert w3.favorites == ["eq3"]
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


def test_admin_flag_roundtrip(tmp_path):
    path = str(tmp_path / "ui_state.json")
    s = local.LocalState(path)
    assert s.admin is None                      # 정하지 않음 (배포본 설치 여부로 정한다)
    s.admin = False
    s.save()
    assert local.LocalState(path).admin is False
    s.admin = True
    s.save()
    assert local.LocalState(path).admin is True


def test_update_key_roundtrip_and_validation(tmp_path):
    path = str(tmp_path / "ui_state.json")
    s = local.LocalState(path)
    assert s.update_key == ""
    s.update_key = "AB" * 32
    assert s.save()
    assert local.LocalState(path).update_key == "ab" * 32          # 소문자로 읽는다
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"update_key": "not-a-key"}, f)
    assert local.LocalState(path).update_key == ""                 # 형식이 틀리면 없는 것으로 본다
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


def test_update_key_is_not_wiped_by_another_window(tmp_path):
    # 먼저 켠 창(키를 모름)이 닫히며 저장해도, 그사이 다른 창이 기억한 서명 키를 지우지 않는다
    path = str(tmp_path / "ui_state.json")
    old_window = local.LocalState(path)
    new_window = local.LocalState(path)
    new_window.update_key = "ab" * 32
    assert new_window.save()
    old_window.group = "1라인"
    assert old_window.save()
    assert local.LocalState(path).update_key == "ab" * 32
    # 이 창이 직접 바꾼 것(--forget-update-key 등)은 그대로 저장된다
    forget = local.LocalState(path)
    forget.update_key = ""
    assert forget.save()
    old_window.save()
    assert local.LocalState(path).update_key == ""

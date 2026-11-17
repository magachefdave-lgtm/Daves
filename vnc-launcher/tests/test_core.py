import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher import core, secret
from launcher.core import Config, Equipment, EquipmentStore, StoreError


def eq(name="EQ-01", host="10.0.0.1", **kw):
    return Equipment(name=name, host=host, **kw)


@pytest.fixture
def store(tmp_path):
    return EquipmentStore(str(tmp_path / "equipment.json"))


def test_add_update_delete_roundtrip(store):
    a, b = eq(), eq("EQ-02", "10.0.0.2", os="Linux")
    store.add(a)
    store.add(b)
    a.memo = "식각"
    store.update(a)
    assert [(e.name, e.memo) for e in store.load()] == [("EQ-01", "식각"), ("EQ-02", "")]
    assert store.delete([a.id]) == 1
    assert [e.name for e in store.load()] == ["EQ-02"]


def test_two_pcs_do_not_overwrite_each_other(store):
    other_pc = EquipmentStore(store.path)
    store.load()                      # 내 PC가 먼저 읽어 둔 뒤
    other_pc.add(eq("EQ-A"))          # 다른 PC가 추가
    store.add(eq("EQ-B", "10.0.0.2"))  # 내 PC가 추가 - EQ-A가 남아 있어야 한다
    assert {e.name for e in store.load()} == {"EQ-A", "EQ-B"}


def test_groups_order_and_empty_groups_persist(store):
    a, b, c = eq("A", group="2라인"), eq("B", "10.0.0.2", group="10라인"), eq("C", "10.0.0.3")
    for e in (a, b, c):
        store.add(e)
    assert store.groups == ["2라인", "10라인"]           # 처음에는 숫자 순
    store.apply_groups(["10라인", "빈 그룹", "2라인"], {})
    fresh = EquipmentStore(store.path)
    fresh.load()
    assert fresh.groups == ["10라인", "빈 그룹", "2라인"]  # 순서와 빈 그룹이 남는다
    fresh.add(eq("D", "10.0.0.4", group="새 라인"))        # 다른 동작이 순서를 지우지 않는다
    assert EquipmentStore(store.path).load() and fresh.groups == ["10라인", "빈 그룹", "2라인", "새 라인"]


def test_move_and_apply_groups(store):
    a, b, c = eq("A", group="G1"), eq("B", "10.0.0.2", group="G1"), eq("C", "10.0.0.3", group="G2")
    for e in (a, b, c):
        store.add(e)
    assert store.move([a.id, c.id], "G3") == 2
    assert {e.name: e.group for e in store.load()} == {"A": "G3", "B": "G1", "C": "G3"}
    assert store.groups == ["G1", "G2", "G3"]
    late = eq("D", "10.0.0.4", group="G2")       # 그룹 관리 화면을 연 뒤 다른 PC가 추가
    # G1 -> 라인1 이름 변경, G3 삭제(C는 그룹 없음으로), A는 라인1로
    other = EquipmentStore(store.path)
    other.add(late)
    store.apply_groups(["라인1", "G2"], {a.id: "라인1", b.id: "라인1", c.id: "G3"})
    assert {e.name: e.group for e in store.load()} == {"A": "라인1", "B": "라인1", "C": "", "D": "G2"}
    assert store.groups == ["라인1", "G2"]
    assert store.move([a.id], "") == 1 and store.load()[0].group == ""


def test_duplicate_name_rejected(store):
    store.add(eq())
    with pytest.raises(StoreError, match="같은 설비명"):
        store.add(eq("eq-01", "10.0.0.9"))


@pytest.mark.parametrize("host", ["", "-evil", "10.0.0.1 -x", "a/b", "한글", "10.0.0..1", "a" * 64 + ".lan"])
def test_bad_host_rejected(store, host):
    with pytest.raises(StoreError):
        store.add(eq(host=host))


@pytest.mark.parametrize("port", [0, 70000, -1])
def test_bad_port_rejected(store, port):
    with pytest.raises(StoreError, match="포트"):
        store.add(eq(port=port))


def test_corrupt_file_reports_backup(store):
    store.add(eq())
    store.add(eq("EQ-02", "10.0.0.2"))
    with open(store.path, "w") as f:
        f.write("{broken")
    with pytest.raises(StoreError, match=r"\.bak"):
        store.load()
    assert [e.name for e in EquipmentStore(store.path + ".bak").load()] == ["EQ-01"]


def test_share_path():
    assert core.share_path(eq()) == r"\\10.0.0.1"
    assert core.share_path(eq(share="D$")) == r"\\10.0.0.1\D$"
    assert core.share_path(eq(share="/Data/Log/")) == r"\\10.0.0.1\Data\Log"
    assert core.share_path(eq(share=r"\\nas\recipes")) == r"\\nas\recipes"
    with pytest.raises(ValueError):
        core.share_path(eq(os="Linux"))


def test_share_can_be_turned_off_per_equipment(store, tmp_path):
    a = eq("EQ-01", share_enabled=False)
    store.add(a)
    store.add(eq("EQ-02", "10.0.0.2"))
    loaded = {e.name: e for e in store.load()}
    assert not core.has_share(loaded["EQ-01"]) and core.has_share(loaded["EQ-02"])
    assert not core.has_share(eq(os="Linux"))
    # 공유폴더사용 열이 없는 CSV로 고쳐도 꺼 둔 설정이 남는다
    p = tmp_path / "in.csv"
    p.write_text("설비명,IP\nEQ-01,10.0.0.9\n", encoding="utf-8")
    store.import_csv(str(p))
    assert store.load()[0].host == "10.0.0.9" and not store.load()[0].share_enabled
    # 열이 있으면 그 값을 따른다 (내보내기 -> 가져오기 왕복 포함)
    out = str(tmp_path / "out.csv")
    core.export_csv(store.load(), out)
    other = EquipmentStore(str(tmp_path / "other.json"))
    other.import_csv(out)
    assert [e.share_enabled for e in other.load()] == [False, True]


def test_new_title():
    assert core.new_title("ETCH-01", "desk - TightVNC Viewer") == "[ETCH-01] desk - TightVNC Viewer"
    assert core.new_title("ETCH-01", "[ETCH-01] desk - TightVNC Viewer") is None
    assert core.new_title("ETCH-1", "[ETCH-10] x") == "[ETCH-1] [ETCH-10] x"


def test_build_command_per_viewer():
    e = eq(port=5901)
    cmd = lambda t, pw="": core.build_command(Config(viewer_type=t, viewer_path="v.exe"), e, pw)
    assert cmd("tightvnc") == ["v.exe", "-host=10.0.0.1", "-port=5901"]
    assert cmd("tightvnc", "p w") == ["v.exe", "-host=10.0.0.1", "-port=5901", "-password=p w"]
    assert cmd("ultravnc", "pw") == ["v.exe", "-connect", "10.0.0.1::5901", "-password", "pw", "-nostatus"]
    loud = Config(viewer_type="ultravnc", viewer_path="v.exe", quiet_connect=False)
    assert core.build_command(loud, e) == ["v.exe", "-connect", "10.0.0.1::5901"]
    assert cmd("realvnc", "pw") == ["v.exe", "10.0.0.1::5901"]  # 비밀번호 전달 불가 뷰어


def test_build_command_custom_and_extra():
    cfg = Config(viewer_type="custom", viewer_path="v.exe", custom_args="{host}:{port}",
                 custom_pw_args="/pw {password}", extra_args="-fullscreen")
    assert core.build_command(cfg, eq(), "{host}") == ["v.exe", "10.0.0.1:5900", "/pw", "{host}", "-fullscreen"]


def test_build_command_needs_viewer_path():
    with pytest.raises(StoreError, match="설정"):
        core.build_command(Config(), eq())


def test_csv_import_cp949_and_merge(store, tmp_path):
    store.add(eq("EQ-01", "10.0.0.1", memo="old"))
    p = tmp_path / "in.csv"
    p.write_bytes("설비명,그룹,IP,포트,OS,공유폴더,메모\r\n"
                  "EQ-01,1라인,10.0.0.11,5900.0,windows,D$,새 메모\r\n"
                  "EQ-02,2라인,10.0.0.12,,리눅스,,\r\n"
                  ",,,,,,\r\n"
                  "EQ-03,2라인,,5900,Windows,,\r\n".encode("cp949"))
    added, updated, problems, passwords = store.import_csv(str(p))
    assert (added, updated) == (1, 1) and passwords == {}
    assert len(problems) == 1 and "5번째 줄" in problems[0]
    items = {e.name: e for e in store.load()}
    assert (items["EQ-01"].host, items["EQ-01"].share, items["EQ-01"].port) == ("10.0.0.11", "D$", 5900)
    assert (items["EQ-02"].os, items["EQ-02"].port) == ("Linux", 5900)


def test_csv_export_import_roundtrip(store, tmp_path):
    src = [eq("설비-가", "10.0.0.1", group="A", share="D$", memo="쉼표, 포함"),
           eq("설비-나", "10.0.0.2", os="Linux", port=5902)]
    p = str(tmp_path / "out.csv")
    core.export_csv(src, p)
    assert open(p, "rb").read().startswith(b"\xef\xbb\xbf")
    store.import_csv(p)
    strip = lambda e: (e.name, e.group, e.host, e.port, e.os, e.share, e.memo)
    assert [strip(e) for e in store.load()] == [strip(e) for e in src]


def test_csv_with_password_column(store, tmp_path):
    old = eq("EQ-01", "10.0.0.1")
    store.add(old)
    p = tmp_path / "in.csv"
    # 비밀번호는 16진수 id 에 우연히 나올 수 없는 글자로 (예전 "aaa" 는 id 와 겹쳐 가끔 실패했다)
    p.write_bytes("설비명,IP,비밀번호\r\nEQ-01,10.0.0.1,p@ss!\r\nEQ-02,10.0.0.2, b b \r\n"
                  "EQ-03,10.0.0.3,\r\nEQ-04,,zzz\r\n".encode("cp949"))
    added, updated, problems, passwords = store.import_csv(str(p))
    ids = {e.name: e.id for e in store.load()}
    assert (added, updated, len(problems)) == (2, 1, 1)
    assert passwords == {old.id: "p@ss!", ids["EQ-02"]: "b b"}   # 빈칸 = 건드리지 않음, 오류 줄 제외
    assert "p@ss" not in open(store.path, encoding="utf-8").read()  # 목록 파일에는 넣지 않는다


def test_csv_password_only_file(store, tmp_path):
    a, b = eq("EQ-01", "10.0.0.1", memo="유지"), eq("EQ-02", "10.0.0.2")
    store.add(a)
    store.add(b)
    p = tmp_path / "pw.csv"
    p.write_text("설비명,비밀번호\neq-01,p1\nEQ-02,\n없는설비,p3\n", encoding="utf-8-sig")
    added, updated, problems, passwords = store.import_csv(str(p))
    assert (added, updated) == (0, 0) and passwords == {a.id: "p1"}
    assert len(problems) == 1 and "없는설비" in problems[0]
    assert [(e.name, e.host, e.memo) for e in store.load()] == [("EQ-01", "10.0.0.1", "유지"), ("EQ-02", "10.0.0.2", "")]


def test_csv_missing_columns(store, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(StoreError, match="설비명"):
        store.import_csv(str(p))


def test_config_roundtrip_with_bom(tmp_path):
    p = str(tmp_path / "config.ini")
    cfg = Config(viewer_type="ultravnc", viewer_path=r"C:\Program Files\uvnc bvba\UltraVNC\vncviewer.exe",
                 extra_args="-fullscreen", equipment_file=r"\\서버\공유\equipment.json",
                 check_on_start=False, check_timeout=2.5, custom_args="%h {host}",
                 show_share=False, title_with_name=False, quiet_connect=False)
    core.save_config(cfg, p)
    assert core.load_config(p) == cfg
    raw = open(p, "rb").read()
    open(p, "wb").write(b"\xef\xbb\xbf" + raw)  # 메모장으로 저장한 경우
    assert core.load_config(p) == cfg


def test_password_store(tmp_path):
    xor = lambda b: bytes(c ^ 0x5A for c in b)
    ps = secret.PasswordStore(str(tmp_path / "pw.json"), xor, xor)
    ps.set("a", "비번 1")
    ps.set("b", "x")
    assert ps.get("a") == "비번 1" and ps.has("b")
    assert "비번" not in open(ps.path, encoding="utf-8").read()
    ps.set_many({"b": "y", "c": "z"})
    assert (ps.get("a"), ps.get("b"), ps.get("c")) == ("비번 1", "y", "z")
    ps.set("a", "")
    ps.delete(["b"])
    assert ps.get("a") == "" and not ps.has("b")


def test_is_reachable():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert core.is_reachable("127.0.0.1", port, 1.0)
    srv.close()
    assert not core.is_reachable("127.0.0.1", port, 0.5)


# ---------------------------------------------------------------- 주의 표시 · RDP ---

def test_caution_and_rdp_fields_roundtrip(store, tmp_path):
    store.add(eq("EQ-01", caution="  PM\n중  ", rdp_enabled=False))
    store.add(eq("EQ-02", "10.0.0.2", os="Linux"))
    a, b = store.load()
    assert (a.caution, a.rdp_enabled) == ("PM 중", False)          # 줄바꿈·여백 정리
    assert not core.has_rdp(a) and not core.has_rdp(b)              # 꺼 둠 / Linux
    assert core.has_rdp(eq()) and core.rdp_command(eq()) == ["mstsc.exe", "/v:10.0.0.1"]
    assert Equipment.from_dict({"name": "x", "host": "h", "caution": "가" * 99}).caution == "가" * core.CAUTION_MAX
    out = str(tmp_path / "out.csv")
    core.export_csv(store.load(), out)
    other = EquipmentStore(str(tmp_path / "other.json"))
    other.import_csv(out)
    assert [(e.caution, e.rdp_enabled) for e in other.load()] == [("PM 중", False), ("", True)]


def test_set_fields_changes_only_given_fields(store):
    a, b = eq("A", memo="m"), eq("B", "10.0.0.2")
    store.add(a)
    store.add(b)
    other = EquipmentStore(store.path)
    other.load()
    a.memo = "다른 PC가 고친 메모"
    EquipmentStore(store.path).update(a)                        # 그 사이 다른 PC가 메모를 고침
    assert other.set_fields([a.id, b.id], caution="PM 중") == 2
    assert other.set_fields([a.id, b.id], caution="PM 중") == 0  # 같은 값이면 쓰지 않는다
    got = {e.name: e for e in store.load()}
    assert (got["A"].caution, got["A"].memo, got["B"].caution) == ("PM 중", "다른 PC가 고친 메모", "PM 중")
    assert other.set_fields([b.id], share_enabled=False, rdp_enabled=False) == 1
    assert not store.load()[1].share_enabled and not store.load()[1].rdp_enabled


def test_csv_absent_columns_keep_existing_values(store, tmp_path):
    a = eq("EQ-01", group="식각", memo="유지", caution="PM 중", share="D$", share_enabled=False)
    store.add(a)
    p = tmp_path / "in.csv"
    p.write_text("설비명,IP,메모\nEQ-01,10.0.0.9,새 메모\nEQ-02,10.0.0.2\n", encoding="utf-8")
    store.import_csv(str(p))
    got = {e.name: e for e in store.load()}
    e1 = got["EQ-01"]
    assert (e1.host, e1.memo) == ("10.0.0.9", "새 메모")                       # 있는 열은 고친다
    assert (e1.group, e1.caution, e1.share, e1.share_enabled) == ("식각", "PM 중", "D$", False)
    assert got["EQ-02"].memo == ""                                              # 칸이 모자란 새 줄


# ---------------------------------------------------------------- 편집 잠금 ---

def test_lock_set_check_and_survives_other_writes(store):
    store.add(eq("A"))
    assert core.check_pin(None, "아무거나")           # 잠금이 없으면 항상 통과
    store.set_lock("1234")
    raw = open(store.path, encoding="utf-8").read()
    assert "1234" not in raw and '"lock"' in raw       # PIN 자체는 남기지 않는다
    other = EquipmentStore(store.path)
    other.add(eq("B", "10.0.0.2"))                     # 다른 PC의 편집이 잠금을 지우지 않는다
    other.move([other.load()[0].id], "G")
    other.set_fields([other.load()[0].id], caution="PM 중")
    fresh = EquipmentStore(store.path)
    fresh.load()
    assert core.check_pin(fresh.lock, "1234") and not core.check_pin(fresh.lock, "1235")
    fresh.set_lock(None)
    fresh.load()
    assert fresh.lock is None and len(fresh.load()) == 2


def test_broken_lock_is_ignored(store):
    store.add(eq("A"))
    import json
    data = json.load(open(store.path, encoding="utf-8"))
    data["lock"] = {"salt": "zz", "hash": 3}
    json.dump(data, open(store.path, "w", encoding="utf-8"))
    store.load()
    assert store.lock is None                         # 아무도 편집 못 하게 되는 것보다 낫다


# ---------------------------------------------------------------- 날짜별 백업 ---

def test_daily_backup_once_per_day_and_pruned(store, monkeypatch):
    day = ["2026-11-01"]
    store.today = lambda: day[0]
    store.add(eq("A"))                                 # 파일이 처음 생길 때는 백업할 것이 없다
    assert not os.path.isdir(store.backup_dir())
    store.add(eq("B", "10.0.0.2"))                     # 그날 처음 바꾸기 직전 = A 만 있는 상태
    store.add(eq("C", "10.0.0.3"))                     # 같은 날 두 번째 변경은 백업하지 않는다
    first = os.path.join(store.backup_dir(), "equipment_2026-11-01.json")
    assert [e.name for e in EquipmentStore(first).load()] == ["A"]
    for d in range(2, 2 + core.BACKUP_KEEP + 3):
        day[0] = f"2026-11-{d:02d}"
        store.set_fields([store.load()[0].id], memo=f"m{d}")
    names = sorted(os.listdir(store.backup_dir()))
    assert len(names) == core.BACKUP_KEEP
    assert names[-1] == f"equipment_2026-11-{1 + core.BACKUP_KEEP + 3:02d}.json"
    assert "equipment_2026-11-01.json" not in names    # 오래된 것부터 지운다


def test_backup_failure_does_not_block_save(store, monkeypatch):
    store.add(eq("A"))
    monkeypatch.setattr(core.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(StoreError):                    # .bak 복사도 같은 함수를 쓰므로 막히면 오류
        store.add(eq("B", "10.0.0.2"))
    monkeypatch.undo()
    blocker = os.path.join(os.path.dirname(store.path), "not-a-folder")
    open(blocker, "w").close()                         # 파일 아래에는 폴더를 만들 수 없다 (어느 OS든)
    monkeypatch.setattr(store, "backup_dir", lambda: os.path.join(blocker, "backup"))
    store.today = lambda: "2026-12-01"
    store.add(eq("B", "10.0.0.2"))                     # 백업 폴더를 못 만들어도 저장은 된다
    assert [e.name for e in store.load()] == ["A", "B"]


def test_config_new_options_roundtrip(tmp_path):
    p = str(tmp_path / "config.ini")
    cfg = Config(show_rdp=True, auto_check_minutes=5, connection_log=False)
    core.save_config(cfg, p)
    assert core.load_config(p) == cfg
    text = open(p, encoding="utf-8").read().replace("auto_check_minutes = 5", "auto_check_minutes = 999")
    open(p, "w", encoding="utf-8").write(text)
    assert core.load_config(p).auto_check_minutes == 60
    assert core.load_config(str(tmp_path / "none.ini")) == Config()     # 기본: RDP 끔, 1분, 이력 켬
    assert (Config().show_rdp, Config().auto_check_minutes, Config().connection_log) == (False, 1, True)


def test_is_reachable_never_raises_on_bad_input():
    assert core.is_reachable("10.0.0..1", 5900, 0.2) is False     # idna 가 UnicodeError 를 낸다
    assert core.is_reachable("127.0.0.1", 70000, 0.2) is False    # 범위 밖 포트는 OverflowError


def test_update_with_base_keeps_changes_made_elsewhere(store):
    a = eq("EQ-07", memo="old")
    store.add(a)
    opened = store.load()[0]                       # PC A 가 수정 화면을 연 때의 내용
    EquipmentStore(store.path).set_fields([a.id], caution="접속 금지")   # 그 사이 PC B
    EquipmentStore(store.path).move([a.id], "식각")
    edited = Equipment.from_dict({**core.asdict(opened), "memo": "새 메모"})
    store.update(edited, base=opened)
    got = store.load()[0]
    assert (got.memo, got.caution, got.group) == ("새 메모", "접속 금지", "식각")
    cleared = Equipment.from_dict({**core.asdict(opened), "caution": "PM 중"})
    store.update(cleared, base=opened)             # 직접 바꾼 항목은 반영된다
    assert store.load()[0].caution == "PM 중"
    store.update(Equipment.from_dict({**core.asdict(opened), "caution": ""}))   # base 없이 = 통째로
    assert store.load()[0].caution == ""


def test_csv_caution_only_for_new_equipment(store, tmp_path):
    store.add(eq("EQ-01", caution="PM 중"))
    p = tmp_path / "in.csv"
    p.write_text("설비명,IP,주의\nEQ-01,10.0.0.1,\nEQ-02,10.0.0.2,점검 중\n", encoding="utf-8")
    store.import_csv(str(p))
    got = {e.name: e.caution for e in store.load()}
    assert got == {"EQ-01": "PM 중", "EQ-02": "점검 중"}    # 예전 CSV 로 현장 표시를 지우지 않는다

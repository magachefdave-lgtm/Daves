import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher import pack, update


def test_versions():
    assert update.is_newer("1.4.0", "1.3.0") and update.is_newer("1.10", "1.9.9")
    assert update.is_newer("v2", "1.99") and not update.is_newer("1.3", "1.3.0")
    assert not update.is_newer("1.3.0", "1.4.0") and update.parse_version("1.4.0-beta") == (1, 4, 0)


@pytest.fixture
def setup(tmp_path):
    new = tmp_path / "build" / "EquipmentRemote.exe"
    new.parent.mkdir()
    new.write_bytes(b"MZ new version " + os.urandom(4000))
    app = tmp_path / "user" / "EquipmentRemote.exe"
    app.parent.mkdir()
    app.write_bytes(b"MZ old version")
    return new, app, tmp_path / "server", tmp_path / "local"


def test_publish_then_install(setup):
    new, app, server, local = setup
    assert update.read_latest(str(server.parent)) is None           # 아직 올린 것 없음
    with pytest.raises(update.UpdateError):
        update.read_latest(str(server / "없는 폴더"))
    info = update.publish(str(new), str(server), "1.4.0", "  알림 개선  ")
    assert info["notes"] == "알림 개선" and info["size"] == new.stat().st_size
    assert update.read_latest(str(server)) == info
    target = update.install(str(server), info, str(app), str(local))
    assert target == str(app) and app.read_bytes() == new.read_bytes()
    assert (app.parent / "EquipmentRemote.exe.old").read_bytes() == b"MZ old version"
    assert not (app.parent / "EquipmentRemote.exe.new").exists()
    update.cleanup_old(str(app))
    assert not (app.parent / "EquipmentRemote.exe.old").exists()


def test_install_rejects_wrong_file_and_keeps_old(setup):
    new, app, server, local = setup
    info = update.publish(str(new), str(server), "1.4.0", "")
    (server / "EquipmentRemote.exe").write_bytes(b"MZ half-uploaded")   # 그 사이 바뀌거나 끊긴 경우
    with pytest.raises(update.UpdateError, match="다시 시도"):
        update.install(str(server), info, str(app), str(local))
    assert app.read_bytes() == b"MZ old version" and not (app.parent / "EquipmentRemote.exe.new").exists()


def test_install_falls_back_when_folder_not_writable(setup, monkeypatch):
    new, app, server, local = setup
    info = update.publish(str(new), str(server), "1.4.0", "")
    monkeypatch.setattr(update, "_writable", lambda folder: False)
    target = update.install(str(server), info, str(app), str(local))
    assert target == str(local / "EquipmentRemote.exe") and app.read_bytes() == b"MZ old version"


def test_publish_strips_deploy_package(setup):
    new, app, server, local = setup
    packed = new.parent / "배포본.exe"
    pack.write_pack(str(new), str(packed), pack.make_payload({}, None, {"x": "secret"}, ""))
    update.publish(str(packed), str(server), "1.4.0", "")
    uploaded = (server / "EquipmentRemote.exe").read_bytes()
    assert uploaded == new.read_bytes() and pack.read_pack(str(server / "EquipmentRemote.exe")) is None
    assert json.load(open(server / "latest.json", encoding="utf-8"))["version"] == "1.4.0"

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


# ------------------------------------------------------------------ 서명 ---

from launcher import sign  # noqa: E402

_ident = (lambda b: b)


def _signed(setup, seed=None, version="1.4.7", notes="서명 시험"):
    new, _app, server, _local = setup
    seed = seed or sign.generate_seed()
    info = update.publish(str(new), str(server), version, notes, signing_seed=seed)
    return seed, info, update.read_latest(str(server))


def test_publish_signed_and_verify(setup):
    seed, info, read = _signed(setup)
    pub = sign.public_key(seed).hex()
    assert read == info and info["key"] == pub and len(info["signature"]) == 128
    assert update.verify_info(read, None) == ("trust_new", pub)
    assert update.verify_info(read, "") == ("trust_new", pub)
    assert update.verify_info(read, pub) == ("ok", pub)
    assert update.verify_info(read, pub.upper()) == ("ok", pub)           # 대소문자 무관
    other = sign.public_key(sign.generate_seed()).hex()
    assert update.verify_info(read, other) == ("bad", pub)                # 다른 관리자 키


def test_publish_unsigned_is_legacy(setup):
    new, _app, server, _local = setup
    info = update.publish(str(new), str(server), "1.4.7", "")
    assert "key" not in info and "signature" not in info
    assert update.verify_info(info, None) == ("unsigned", "")
    pinned = sign.public_key(sign.generate_seed()).hex()
    assert update.verify_info(info, pinned) == ("bad", "")                # 믿는 키가 있으면 서명 필수


@pytest.mark.parametrize("field, value", [
    ("version", "9.9.9"), ("notes", "다른 내용"), ("sha256", "0" * 64), ("size", 1), ("file", "x.exe"),
    ("extra", "추가된 항목"),
])
def test_verify_rejects_tampered_fields(setup, field, value):
    seed, _info, read = _signed(setup)
    pub = sign.public_key(seed).hex()
    tampered = dict(read, **{field: value})
    assert update.verify_info(tampered, pub)[0] == "bad"
    assert update.verify_info(tampered, None)[0] == "bad"


def test_verify_rejects_malformed(setup):
    seed, _info, read = _signed(setup)
    pub = read["key"]
    other_seed = sign.generate_seed()
    swapped = dict(read, key=sign.public_key(other_seed).hex())             # 키만 바꿔치기
    assert update.verify_info(swapped, None)[0] == "bad"
    resigned = dict(read, key=sign.public_key(other_seed).hex())           # 다른 키로 제대로 서명
    resigned["signature"] = sign.sign(other_seed, update.signed_bytes(resigned)).hex()
    assert update.verify_info(resigned, pub)[0] == "bad"
    assert update.verify_info(resigned, None)[0] == "trust_new"             # 처음 보는 PC 는 믿는다 (TOFU)
    for bad in (dict(read, signature="zz"), dict(read, key=None), dict(read, signature=None),
                dict(read, key=12), {k: v for k, v in read.items() if k != "signature"},
                {k: v for k, v in read.items() if k != "key"}, dict(read, signature=read["signature"][:-2])):
        assert update.verify_info(bad, None)[0] == "bad"
        assert update.verify_info(bad, pub)[0] == "bad"
    assert update.verify_info(None, None)[0] == "bad"


def test_signed_install_end_to_end(setup):
    new, app, server, local = setup
    seed, _info, read = _signed(setup)
    assert update.verify_info(read, read["key"])[0] == "ok"
    target = update.install(str(server), read, str(app), str(local))
    assert target == str(app) and app.read_bytes() == new.read_bytes()


def test_publish_rejects_bad_seed_before_touching_folder(setup):
    new, _app, server, _local = setup
    with pytest.raises(update.UpdateError):
        update.publish(str(new), str(server), "1.4.7", "", signing_seed=b"short")
    assert not server.exists()


def test_signed_bytes_ignores_signature_and_order():
    a = {"version": "1", "notes": "한글", "signature": "x"}
    b = {"notes": "한글", "version": "1"}
    assert update.signed_bytes(a) == update.signed_bytes(b) == '{"notes":"한글","version":"1"}'.encode("utf-8")


def _store(tmp_path, name="key.json"):
    return update.SigningKeyStore(str(tmp_path / "admin" / name), encrypt=_ident, decrypt=_ident)


def test_key_store_create_and_load(tmp_path):
    ks = _store(tmp_path)
    assert not ks.exists() and ks.public_hex() == ""
    with pytest.raises(update.UpdateError, match="서명 키가 없습니다"):
        ks.load()
    seed = ks.create()
    assert ks.exists() and ks.load() == seed and ks.public_hex() == sign.public_key(seed).hex()
    data = json.load(open(ks.path, encoding="utf-8"))
    assert data["format"] == 1 and data["protected"] is True and "created" in data
    with pytest.raises(update.UpdateError, match="이미"):
        ks.create()                                         # 조용히 바꾸지 않는다
    assert ks.load() == seed


def test_key_store_default_is_unprotected_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "linux")
    ks = update.SigningKeyStore(str(tmp_path / "k.json"))
    seed = ks.create()
    assert ks.load() == seed and json.load(open(ks.path, encoding="utf-8"))["protected"] is False


def test_key_store_cannot_decrypt_or_corrupt(tmp_path):
    ks = _store(tmp_path)
    ks.create()

    def fail(_b):
        raise OSError("DPAPI 처리 실패")
    other_account = update.SigningKeyStore(ks.path, encrypt=_ident, decrypt=fail)
    with pytest.raises(update.UpdateError, match="풀 수 없습니다"):
        other_account.load()
    data = json.load(open(ks.path, encoding="utf-8"))
    data["public"] = sign.public_key(sign.generate_seed()).hex()          # 공개 키가 안 맞음
    json.dump(data, open(ks.path, "w", encoding="utf-8"))
    with pytest.raises(update.UpdateError, match="손상"):
        ks.load()
    open(ks.path, "w", encoding="utf-8").write("{깨짐")
    with pytest.raises(update.UpdateError, match="읽을 수 없습니다"):
        ks.load()
    assert ks.public_hex() == ""


def test_key_export_import(tmp_path):
    ks = _store(tmp_path)
    seed = ks.create()
    backup = tmp_path / "서명키_백업.json"
    with pytest.raises(update.UpdateError):
        ks.export(str(backup), "")
    ks.export(str(backup), "line3")
    text = backup.read_text(encoding="utf-8")
    assert seed.hex() not in text and json.loads(text)["public"] == ks.public_hex()

    other_pc = _store(tmp_path, "other.json")
    with pytest.raises(ValueError, match="키 백업 암호"):
        other_pc.import_file(str(backup), "line4")
    assert not other_pc.exists()
    assert other_pc.import_file(str(backup), "line3") == ks.public_hex()
    assert other_pc.load() == seed
    assert other_pc.import_file(str(backup), "line3") == ks.public_hex()   # 같은 키는 다시 가져와도 됨

    third = _store(tmp_path, "third.json")
    third_seed = third.create()
    with pytest.raises(update.UpdateError, match="다른 업데이트 서명 키"):
        third.import_file(str(backup), "line3")
    assert third.load() == third_seed                                      # 그대로 둠
    third.import_file(str(backup), "line3", replace=True)
    assert third.load() == seed

    not_key = tmp_path / "다른파일.json"
    not_key.write_text('{"version": "1"}', encoding="utf-8")
    with pytest.raises(update.UpdateError, match="읽을 수 없습니다"):
        third.import_file(str(not_key), "line3")
    with pytest.raises(update.UpdateError):
        third.import_file(str(tmp_path / "없음.json"), "line3")


def test_key_store_seed_signs_publish(tmp_path, setup):
    ks = _store(tmp_path)
    seed = ks.create()
    _s, info, read = _signed(setup, seed=ks.load())
    assert update.verify_info(read, ks.public_hex())[0] == "ok"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_key_store_uses_dpapi_on_windows(tmp_path):
    store = update.SigningKeyStore(str(tmp_path / update.KEY_FILE))
    seed = store.create()
    raw = (tmp_path / update.KEY_FILE).read_text(encoding="utf-8")
    assert json.loads(raw)["protected"] is True
    assert seed.hex() not in raw and __import__("base64").b64encode(seed).decode() not in raw
    assert update.SigningKeyStore(str(tmp_path / update.KEY_FILE)).load() == seed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_password_store_uses_dpapi_on_windows(tmp_path):
    from launcher import secret
    store = secret.PasswordStore(str(tmp_path / "passwords.json"))
    store.set("eq1", "비번-1")
    assert "비번" not in (tmp_path / "passwords.json").read_text(encoding="utf-8")
    assert secret.PasswordStore(str(tmp_path / "passwords.json")).get("eq1") == "비번-1"

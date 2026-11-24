import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher import secret

same = lambda b: b   # 시험용: 암호화하지 않는다


@pytest.fixture
def ps(tmp_path):
    return secret.PasswordStore(str(tmp_path / "passwords.json"), same, same)


def test_save_keeps_previous_file_as_bak(ps):
    ps.set("a", "1")
    ps.set("b", "2")
    with open(ps.path + ".bak", encoding="utf-8") as f:
        assert set(json.load(f)) == {"a"}
    assert ps.get("a") == "1" and ps.get("b") == "2"
    assert not [n for n in os.listdir(os.path.dirname(ps.path)) if n.endswith(".tmp")]


def test_corrupt_file_falls_back_to_bak_and_is_kept(ps):
    ps.set("a", "1")
    ps.set("b", "2")                      # .bak = {a}
    with open(ps.path, "w", encoding="utf-8") as f:
        f.write('{"a": "MQ==", "b"')       # 쓰다 만 파일
    assert ps.get("a") == "1" and ps.get("b") == ""
    ps.set("c", "3")                      # .bak 내용 위에 더해 저장, 깨진 파일은 따로 남긴다
    assert (ps.get("a"), ps.get("c")) == ("1", "3")
    broken = [n for n in os.listdir(os.path.dirname(ps.path)) if ".broken-" in n]
    assert len(broken) == 1
    with open(os.path.join(os.path.dirname(ps.path), broken[0]), encoding="utf-8") as f:
        assert f.read() == '{"a": "MQ==", "b"'


def test_corrupt_without_bak_starts_empty_but_keeps_copy(ps):
    with open(ps.path, "wb") as f:
        f.write(b"\xff\xfe garbage")
    assert ps.get("a") == "" and not ps.has("a")
    ps.set("a", "1")
    assert ps.get("a") == "1"
    assert [n for n in os.listdir(os.path.dirname(ps.path)) if ".broken-" in n]


def test_non_dict_json_counts_as_corrupt(ps):
    ps.set("a", "1")
    ps.set("b", "2")
    with open(ps.path, "w", encoding="utf-8") as f:
        f.write("[]")
    assert ps.get("a") == "1"


def test_unreadable_file_is_never_overwritten(ps, monkeypatch):
    ps.set("a", "1")
    ps.set("b", "2")
    before = open(ps.path, "rb").read()
    real_open = open

    def locked(path, *a, **k):
        if str(path) == ps.path and (not a or "r" in a[0]):
            raise PermissionError(13, "백신이 잡고 있음")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", locked)
    assert ps.get("a") == "" and not ps.has("a")
    for action in (lambda: ps.set("c", "3"), lambda: ps.set_many({"c": "3"}), lambda: ps.delete(["a"]),
                   lambda: ps.set("a", "")):
        with pytest.raises(OSError):
            action()
    monkeypatch.undo()
    assert open(ps.path, "rb").read() == before
    assert (ps.get("a"), ps.get("b")) == ("1", "2")


def test_failed_write_leaves_old_file_and_no_tmp(ps, monkeypatch):
    ps.set("a", "1")
    monkeypatch.setattr(secret.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        ps.set("b", "2")
    monkeypatch.undo()
    assert ps.get("a") == "1" and ps.get("b") == ""
    assert not [n for n in os.listdir(os.path.dirname(ps.path)) if n.endswith(".tmp")]


def test_tmp_name_is_per_process(ps, monkeypatch):
    seen = []
    real_replace = os.replace
    monkeypatch.setattr(secret.os, "replace", lambda s, d: (seen.append(s), real_replace(s, d)))
    ps.set("a", "1")
    assert seen == [f"{ps.path}.{os.getpid()}.tmp"]


def test_missing_file_is_empty_and_delete_noop(ps):
    assert ps.get("x") == "" and not ps.has("x")
    ps.delete(["x"])
    ps.set("x", "")
    assert not os.path.exists(ps.path)

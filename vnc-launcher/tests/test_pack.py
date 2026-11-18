import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher import pack
from launcher.core import Equipment, EquipmentStore


def test_seal_roundtrip_and_wrong_passphrase():
    data = {"id1": "비번 1", "id2": "p@ss"}
    s = pack.seal(data, "line3")
    assert s["passphrase"] and "p@ss" not in str(s) and "비번" not in str(s)
    assert pack.unseal(s, "line3") == data
    with pytest.raises(ValueError):
        pack.unseal(s, "line4")
    bad = dict(s, data=s["data"][:-4] + "AAAA")
    with pytest.raises(ValueError):
        pack.unseal(bad, "line3")                     # 손상되면 알아챈다
    open_ = pack.seal(data, "")
    assert not open_["passphrase"] and pack.unseal(open_, "") == data


def test_write_and_read_pack(tmp_path):
    exe = tmp_path / "EquipmentRemote.exe"
    exe.write_bytes(b"MZ" + os.urandom(5000))
    assert pack.read_pack(str(exe)) is None
    payload = pack.make_payload({"viewer_type": "ultravnc"}, {"groups": ["A"], "items": []},
                                {"x": "pw"}, "")
    out = tmp_path / "배포.exe"
    pack.write_pack(str(exe), str(out), payload)
    got = pack.read_pack(str(out))
    assert got["id"] == payload["id"] and got["config"] == {"viewer_type": "ultravnc"}
    assert got["password_count"] == 1 and pack.unseal(got["passwords"], "") == {"x": "pw"}
    assert out.read_bytes().startswith(exe.read_bytes())          # 원래 exe 는 그대로 앞에 있다
    # 배포본으로 또 배포본을 만들면 예전 것은 떼고 새것만 붙인다
    again = tmp_path / "배포2.exe"
    p2 = pack.make_payload({"viewer_type": "tightvnc"}, None, None, "")
    pack.write_pack(str(out), str(again), p2)
    assert pack.read_pack(str(again))["id"] == p2["id"]
    assert again.stat().st_size < out.stat().st_size + 2000
    assert again.read_bytes()[:len(exe.read_bytes())] == exe.read_bytes()
    with pytest.raises(pack.PackError):
        pack.write_pack(str(out), str(out), p2)                    # 실행 중인 파일에 덮어쓰기 금지


def test_store_merge(tmp_path):
    store = EquipmentStore(str(tmp_path / "e.json"))
    a = Equipment(name="EQ-01", host="10.0.0.1", memo="mine")
    store.add(a)
    store.add(Equipment(name="EQ-02", host="10.0.0.2"))
    incoming = [dict(id=a.id, name="EQ-01", host="10.0.0.11"),       # 같은 id: 바뀜
                dict(id="other", name="eq-02", host="10.0.0.22"),     # 같은 이름, 다른 id: 배포본 것으로
                dict(id="new", name="EQ-03", host="10.0.0.3", group="B"),
                dict(id="bad", name="EQ-04", host="10.0.0..4")]       # 잘못된 줄은 건너뜀
    assert store.merge(incoming, ["B"]) == (1, 2)
    got = {e.name: e for e in store.load()}
    assert got["EQ-01"].host == "10.0.0.11" and got["eq-02"].id == "other" and "EQ-04" not in got
    assert store.groups == ["B"]

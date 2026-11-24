"""실제 VNC 서버에 대고 "상태 확인이 서버를 막히게 하지 않는가"를 확인한다.

프로그램이 쓰는 상태 확인(launcher.core.probe)을 그대로 여러 번 돌린 뒤, 진짜 VNC 로그인
(비밀번호 인증까지)이 여전히 되는지 본다. "Too many security failures" 같은 차단이 생기면
로그인 단계에서 서버가 거절 이유를 보내므로 그것을 그대로 기록한다.

  python tools/vnc_server_check.py --label tigervnc --port 5901 --password pw \
      --os Linux --check auto --rounds 300 --expect ok

--expect ok      상태 확인 뒤에도 로그인이 돼야 한다 (안 되면 실패 코드로 끝난다)
--expect blocked 차단이 생겨야 한다 (경고 문구가 사실인지 확인할 때)
--expect any     결과만 기록한다
결과는 한 줄 JSON 으로 출력하고, --summary 파일이 있으면 표 한 줄을 덧붙인다.
DES 계산에 pycryptodome 이 필요하다 (pip install pycryptodome). 시험용 도구라 exe 에는 들어가지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from launcher.core import Equipment, check_method, probe  # noqa: E402


def _recv(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("서버가 연결을 끊음")
        buf += chunk
    return buf


def _reason(s: socket.socket) -> str:
    (n,) = struct.unpack("!I", _recv(s, 4))
    return _recv(s, n).decode("utf-8", "replace")


def _des_response(password: str, challenge: bytes) -> bytes:
    from Crypto.Cipher import DES
    key = password.encode("latin-1")[:8].ljust(8, b"\0")
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)   # VNC 인증은 바이트마다 비트 순서를 뒤집은 키를 쓴다
    return DES.new(key, DES.MODE_ECB).encrypt(challenge)


def obfuscate(password: str) -> bytes:
    """VNC 서버들이 비밀번호 파일에 저장하는 형태 (TigerVNC PasswordFile, x11vnc -rfbauth, UltraVNC ini 의 passwd)."""
    from Crypto.Cipher import DES
    return DES.new(bytes.fromhex("e84ad660c4721ae0"), DES.MODE_ECB).encrypt(
        password.encode("latin-1")[:8].ljust(8, b"\0"))


def vnc_login(host: str, port: int, password: str, timeout: float = 10.0) -> tuple[str, str]:
    """("ok"|"blocked"|"failed"|"error", 설명). blocked = 서버가 인증 전에 거절 (차단 목록 등)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            ver = _recv(s, 12)
            if not ver.startswith(b"RFB "):
                return "error", f"VNC 서버가 아님: {ver!r}"
            major, minor = int(ver[4:7]), int(ver[8:11])
            minor = 8 if (major, minor) >= (3, 8) else 7 if (major, minor) >= (3, 7) else 3
            s.sendall(b"RFB 003.%03d\n" % minor)
            if minor >= 7:
                (count,) = struct.unpack("!B", _recv(s, 1))
                if count == 0:
                    return "blocked", _reason(s)
                types = list(_recv(s, count))
                chosen = 2 if 2 in types else 1 if 1 in types else None
                if chosen is None:
                    return "error", f"VNC 비밀번호 인증을 지원하지 않음: {types}"
                s.sendall(bytes([chosen]))
            else:
                (chosen,) = struct.unpack("!I", _recv(s, 4))
                if chosen == 0:
                    return "blocked", _reason(s)
            if chosen == 2:
                s.sendall(_des_response(password, _recv(s, 16)))
                (result,) = struct.unpack("!I", _recv(s, 4))
                if result != 0:
                    why = ""
                    if minor >= 8:
                        try:
                            why = _reason(s)
                        except (OSError, ConnectionError):
                            pass
                    return "failed", why or f"인증 실패 (코드 {result})"
            elif minor >= 8:
                (result,) = struct.unpack("!I", _recv(s, 4))
                if result != 0:
                    return "failed", _reason(s)
            s.sendall(b"\x01")                       # ClientInit (화면 공유)
            w, h = struct.unpack("!HH", _recv(s, 4))
            _recv(s, 16)
            (n,) = struct.unpack("!I", _recv(s, 4))
            name = _recv(s, n).decode("utf-8", "replace")
            return "ok", f"{w}x{h} {name}"
    except (OSError, ConnectionError) as e:
        return "error", str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-passwd", metavar="파일", help="비밀번호 파일만 만들고 끝낸다")
    ap.add_argument("--print-hex", action="store_true", help="UltraVNC ini 용 passwd 값만 출력하고 끝낸다")
    ap.add_argument("--label")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5900)
    ap.add_argument("--password", required=True)
    ap.add_argument("--os", default="Linux", choices=["Linux", "Windows"])
    ap.add_argument("--check", default="auto")
    ap.add_argument("--rounds", type=int, default=300)
    ap.add_argument("--expect", default="ok", choices=["ok", "blocked", "any"])
    ap.add_argument("--wait", type=float, default=20.0, help="서버가 뜰 때까지 기다리는 시간(초)")
    ap.add_argument("--summary")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="확인 사이 간격(초). 0 = 몰아서 (가장 가혹한 경우), 실제 프로그램은 PC 마다 1분에 한 번")
    ap.add_argument("--require-answer", action="store_true", help="상태 확인이 모두 '응답 있음'이어야 한다")
    a = ap.parse_args()
    if a.write_passwd:
        with open(a.write_passwd, "wb") as f:
            f.write(obfuscate(a.password))
        return 0
    if a.print_hex:
        print(obfuscate(a.password).hex().upper() + "00")
        return 0
    if not a.label:
        ap.error("--label 이 필요합니다")

    eq = Equipment(name=a.label, host=a.host, port=a.port, os=a.os, check=a.check)
    method = check_method(eq)

    end = time.monotonic() + a.wait
    while True:                                  # 서버가 다 뜰 때까지 (이 로그인은 성공이라 기록을 남기지 않는다)
        before = vnc_login(a.host, a.port, a.password)
        if before[0] == "ok" or time.monotonic() > end:
            break
        time.sleep(1)
    if before[0] != "ok":
        print(json.dumps({"label": a.label, "error": "시작 전 로그인 실패", "before": before}, ensure_ascii=False))
        return 2

    t0 = time.monotonic()
    answered = 0
    for i in range(a.rounds):
        if i and a.interval:
            time.sleep(a.interval)
        answered += probe(method, a.host, a.port, 1.5)
    took = time.monotonic() - t0
    after = vnc_login(a.host, a.port, a.password)

    res = {"label": a.label, "os": a.os, "check": a.check, "method": method, "rounds": a.rounds, "interval": a.interval,
           "answered": answered, "seconds": round(took, 1), "after": after[0], "detail": after[1]}
    print(json.dumps(res, ensure_ascii=False))
    good = (a.expect == "any" or after[0] == a.expect) and (answered == a.rounds or not a.require_answer)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            pace = f"{a.interval:g}초 간격" if a.interval else "몰아서"
            f.write(f"| {a.label} | {a.os} / {a.check} → {method} | {a.rounds} ({pace}) | {answered} | "
                    f"{after[0]} | {after[1]} | {'통과' if good else '**실패**'} (기대: {a.expect}) |\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())

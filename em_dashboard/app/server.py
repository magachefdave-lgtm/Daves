"""EM 평가 현황 대시보드 - LAN 서버 (Python 표준 라이브러리만 사용).

- 원본 .txt 는 읽기만 한다. 작업자 입력은 store.py 의 로컬 SQLite 에만 저장한다.
- 요청이 올 때 파일의 (mtime, size) 를 보고 바뀌었으면 다시 읽는다. revision 이 올라가면
  브라우저가 /api/status 폴링으로 알아채고 새로 불러온다.
- 파일을 못 읽으면 서버는 죽지 않고, 마지막으로 읽은 데이터를 유지하면서 오류를 화면에 알린다.
- 접속 허용 IP 를 config.ini 에서 제한할 수 있다 (개별 IP 만, 비우면 전체 허용).
"""
import configparser
import json
import mimetypes
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import store as store_mod
from . import txt_reader

APP_VERSION = "0.7.0"

DEFAULT_CONFIG = """\
; EM 평가 현황 대시보드 설정 (메모장으로 수정 가능, UTF-8)
[server]
port = 8080
; 접속을 허용할 PC IP (쉼표 구분, 개별 IP 만). 비우면 모두 허용
allowed_ips =

[data]
; EM .txt DB 경로. 공유 폴더면 \\\\서버\\폴더\\파일.txt 형식
em_txt = data/sample_em.txt
; 작업자 입력 저장 파일 (서버 PC 로컬 디스크에 두세요. 공유 폴더 금지)
overlay_db = data/overlay.db
; 화면 자동 갱신 확인 주기 (초)
poll_seconds = 10
; 행을 구분하는 컬럼 (쉼표 구분). 예) Test,List_no  또는  Test,List_no,Lot_ID
; 작업자 입력 칸(Sdm_no, Test_in 등)은 쓸 수 없습니다.
; 바꾸고 서버를 다시 켜면 이미 저장된 입력값·추가·삭제 기록을 새 기준으로 옮깁니다.
row_key = Test,List_no

[equipment_ovens]
; Micro oven 설비별 Oven 대수 (설비마다 5~24대). Oven 1대 = DUT 보드 1장 = 시료 16개.
; 설비 이름은 현황 표의 "설비" 칸에 작업자가 입력하는 이름과 같게 적습니다.
; 적어 두면 "Oven 사용" 화면에 빈 Oven 수가 나옵니다. 비워 두면 사용 중인 대수만 보입니다.
; 예)
; MO-01 = 24
; MO-02 = 5
"""

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def base_dir():
    """config.ini 와 data/ 를 두는 폴더. exe 로 묶였으면 exe 가 있는 폴더."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def static_dir():
    """화면 파일 폴더. exe 안에 묶였으면 _MEIPASS 아래."""
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "static")


def load_config(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG)
    cp = configparser.ConfigParser()
    cp.optionxform = str  # 테스터 이름 대소문자 유지
    # 메모장이 붙이는 BOM 때문에 설정이 조용히 기본값이 되는 사고를 막는다
    with open(path, encoding="utf-8-sig") as f:
        cp.read_file(f)
    cfg = {
        "port": cp.getint("server", "port", fallback=8080),
        "allowed_ips": [ip.strip() for ip in cp.get("server", "allowed_ips", fallback="").split(",") if ip.strip()],
        "em_txt": cp.get("data", "em_txt", fallback="data/sample_em.txt"),
        "overlay_db": cp.get("data", "overlay_db", fallback="data/overlay.db"),
        "poll_seconds": max(3, cp.getint("data", "poll_seconds", fallback=10)),
        "key_fields": store_mod.parse_key_fields(cp.get("data", "row_key", fallback="")),
    }
    store_mod.set_key_fields(cfg["key_fields"])
    cfg["ovens"] = {}
    for sec in ("equipment_ovens", "tester_ovens"):  # tester_ovens = v0.5.0 이름
        if not cp.has_section(sec):
            continue
        for name, val in cp.items(sec):
            try:
                if int(val) > 0:
                    cfg["ovens"][name] = int(val)
            except ValueError:
                pass
    if not os.path.isabs(cfg["em_txt"]) and not cfg["em_txt"].startswith("\\\\"):
        cfg["em_txt"] = os.path.join(base_dir(), cfg["em_txt"])
    if not os.path.isabs(cfg["overlay_db"]):
        cfg["overlay_db"] = os.path.join(base_dir(), cfg["overlay_db"])
    return cfg


class DataCache:
    """원본 파일을 읽어 두고, 바뀌었을 때만 다시 읽는다."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.fp = None
        self.revision = 0
        self.data = {"rows": [], "quarantine": [], "encoding": None, "size": 0, "mtime": None, "sha256": None}
        self.error = None
        self.loaded_at = None
        self.last_check = 0.0

    def refresh(self, min_interval=1.0):
        with self.lock:
            now = time.time()
            if now - self.last_check < min_interval and self.loaded_at is not None:
                return
            self.last_check = now
            fp = txt_reader.fingerprint(self.path)
            if fp is None:
                self.error = "원본 파일을 찾을 수 없습니다: %s" % self.path
                return
            if fp == self.fp and self.error is None:
                return
            try:
                self.data = txt_reader.read_file(self.path)
                self.fp = fp
                self.error = None
                self.revision += 1
                self.loaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
            except OSError as e:
                self.error = "원본 파일을 읽지 못했습니다 (%s). 마지막으로 읽은 데이터를 보여줍니다." % e.strerror

    def status(self):
        return {"revision": self.revision, "error": self.error, "loaded_at": self.loaded_at,
                "rows": len(self.data["rows"]), "quarantine": len(self.data["quarantine"])}


def make_handler(cfg, cache, store):
    sdir = static_dir()

    def revision():
        return "%d.%d" % (cache.revision, store.counter)

    def merged():
        edits, added, deleted = store.snapshot()
        return store_mod.merge(cache.data["rows"], edits, added, deleted)

    class Handler(BaseHTTPRequestHandler):
        server_version = "EMDashboard/" + APP_VERSION

        def log_message(self, fmt, *args):  # 콘솔을 조용하게
            pass

        def _send(self, code, body, ctype):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False), MIME[".json"])

        def _allowed(self):
            return not cfg["allowed_ips"] or self.client_address[0] in cfg["allowed_ips"] or self.client_address[0] == "127.0.0.1"

        def do_GET(self):
            if not self._allowed():
                return self._send(403, "접속이 허용되지 않은 PC 입니다.", "text/plain; charset=utf-8")
            path = urlparse(self.path).path
            if path == "/api/status":
                cache.refresh()
                return self._json(dict(cache.status(), revision=revision()))
            if path == "/api/data":
                cache.refresh(min_interval=0)
                d = cache.data
                visible, gone = merged()
                return self._json({
                    "rows": visible,
                    "deleted": gone,
                    "quarantine": d["quarantine"],
                    "meta": dict(cache.status(), revision=revision(), source=cfg["em_txt"], encoding=d["encoding"],
                                 size=d["size"], sha256=d["sha256"], version=APP_VERSION, demo=False),
                    "config": {"poll_seconds": cfg["poll_seconds"], "editable": store_mod.EDITABLE,
                               "key_fields": cfg["key_fields"], "ovens": cfg["ovens"]},
                })
            if path == "/api/history":
                return self._json({"items": store.history()})
            if path == "/api/export/txt":
                cache.refresh(min_interval=0)
                enc = "cp949" if (cache.data["encoding"] or "").startswith("cp949") else "utf-8"
                body = store_mod.to_txt(merged()[0], cache.data["quarantine"]).encode(enc, errors="replace")
                name = "EM_DB_merged_%s.txt" % time.strftime("%Y%m%d_%H%M")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=%s" % enc)
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ("/", ""):
                path = "/index.html"
            rel = os.path.normpath(path.lstrip("/"))
            if rel.startswith("..") or os.path.isabs(rel):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            full = os.path.join(sdir, rel)
            if not os.path.isfile(full):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            ext = os.path.splitext(full)[1].lower()
            # Windows 레지스트리의 잘못된 .js MIME 을 피하려고 직접 매핑한다
            ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                return self._send(200, f.read(), ctype)

        def _read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2000000:
                raise store_mod.BatchError(-1, "요청이 너무 큽니다.")
            return json.loads(self.rfile.read(n).decode("utf-8"))

        def do_POST(self):
            if not self._allowed():
                return self._send(403, "접속이 허용되지 않은 PC 입니다.", "text/plain; charset=utf-8")
            path = urlparse(self.path).path
            if path not in ("/api/save", "/api/edit"):
                return self._send(405, "허용되지 않은 요청입니다.", "text/plain; charset=utf-8")
            try:
                req = self._read_json()
                user = str(req.get("user") or "").strip()[:30]
                if not user:
                    raise store_mod.BatchError(-1, "작업자 이름을 먼저 적어 주세요.")
                if path == "/api/edit":  # 칸 하나 저장 (이전 버전 호환)
                    ops = [{"op": "set", "key": req.get("key"), "field": req.get("field"),
                            "value": req.get("value"), "version": req.get("version", 0)}]
                else:
                    ops = req.get("ops")
                cache.refresh(min_interval=0)
                status, payload = store.apply_batch(ops, user, self.client_address[0], cache.data["rows"])
            except store_mod.BatchError as e:
                code = 404 if "찾을 수 없습니다" in str(e) else 400
                return self._json({"error": str(e), "i": e.i}, code)
            except (ValueError, TypeError, AttributeError) as e:
                return self._json({"error": "요청 형식이 잘못되었습니다: %s" % e}, 400)
            if status == "conflict":
                if path == "/api/edit":
                    return self._json({"error": "conflict", "current": payload[0]["current"]}, 409)
                return self._json({"error": "conflict", "conflicts": payload}, 409)
            if path == "/api/edit":
                cur = store.all().get(ops[0]["key"], {}).get(ops[0]["field"])
                return self._json({"ok": True, "state": cur or {"value": "", "version": 0}, "revision": revision()})
            return self._json(dict(payload, ok=True, revision=revision()))

        def do_PUT(self):
            self._send(405, "허용되지 않은 요청입니다.", "text/plain; charset=utf-8")

        do_PATCH = do_DELETE = do_PUT

    return Handler


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def create_server(cfg, cache, store, port_range=20):
    handler = make_handler(cfg, cache, store)
    last_err = None
    for port in range(cfg["port"], cfg["port"] + port_range):
        try:
            return ThreadingHTTPServer(("0.0.0.0", port), handler), port
        except OSError as e:
            last_err = e
    raise last_err


def main(open_browser=True):
    os.chdir(base_dir())
    try:
        cfg = load_config(os.path.join(base_dir(), "config.ini"))
    except ValueError as e:
        print("config.ini 오류: %s" % e)
        input("Enter 를 누르면 닫힙니다.")
        return
    cache = DataCache(cfg["em_txt"])
    cache.refresh(min_interval=0)
    store = store_mod.Store(cfg["overlay_db"])
    try:
        store.backup(os.path.join(os.path.dirname(cfg["overlay_db"]), "backups"))
    except (OSError, Exception) as e:  # 백업 실패로 서버가 안 뜨면 안 된다
        print("주의: 입력값 백업 실패 (%s)" % e)
    try:
        # 원본을 못 읽었을 때 옮기면 기록이 전부 "못 옮김" 이 되므로 다음 시작으로 미룬다
        moved = store.remap_keys(cache.data["rows"]) if cache.data["rows"] and not cache.error else None
        if moved:
            print("행 구분 기준이 바뀌어 저장된 기록을 옮겼습니다: %d건, 못 옮김 %d건 (백업은 data/backups)" % moved)
    except Exception as e:  # 옮기기 실패해도 원래 기록은 그대로 (롤백)
        print("주의: 행 구분 기준 변경 반영 실패 (%s). 이전 기록은 그대로입니다." % e)
    httpd, port = create_server(cfg, cache, store)
    url = "http://%s:%d" % (lan_ip(), port)
    print("EM 평가 현황 대시보드 %s" % APP_VERSION)
    print("원본 파일: %s (읽기 전용)" % cfg["em_txt"])
    print("입력값 저장: %s" % cfg["overlay_db"])
    if cache.error:
        print("주의: " + cache.error)
    if port != cfg["port"]:
        print("설정 포트 %d 가 사용 중이라 %d 로 열었습니다." % (cfg["port"], port))
    print("팀원 접속 주소: %s" % url)
    print("종료: 이 창을 닫거나 Ctrl+C")
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:%d" % port)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

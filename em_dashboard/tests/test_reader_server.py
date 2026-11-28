"""파싱·서버 테스트 (표준 라이브러리 unittest). python -m unittest discover -s tests -v"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import server, store as store_mod, txt_reader  # noqa: E402

ROW16 = "EM,2838,293,LN020LEP,.,.,8/8,128,2024-04-01,2024-04-02,.,zmeit,OIH_H,JYJ,NZKELW124|NWKEJWL(92,BJEE2|TJEK"
ROW17 = "EM,2839,294,LN020LEP,2024-05-01,.,4/4,64,2024-04-01,2024-04-02,2024-04-20,zmeit,OIH_H,JYJ,재측정 필요，확인,LOT1,STR1"


def _read(p):
    with open(p, "rb") as f:
        return f.read()


class ReaderTest(unittest.TestCase):
    def test_16_columns_v12_example(self):
        # [위험] 문서의 실제 예시 행이 컬럼 밀림 없이 읽혀야 한다
        rows, q = txt_reader.parse_text(ROW16)
        self.assertEqual(q, [])
        r = rows[0]
        self.assertEqual(r["List_no"], "293")
        self.assertEqual(r["Test_in"], "")  # '.' -> 빈 값
        self.assertEqual(r["Wafer_in"], "2024-04-01")
        self.assertEqual(r["PKG_Line"], "2024-04-02")
        self.assertEqual(r["Engineer"], "JYJ")
        self.assertEqual(r["비고"], "")
        self.assertEqual(r["Lot_ID"], "NZKELW124|NWKEJWL(92")
        self.assertEqual(r["Structure"], "BJEE2|TJEK")
        self.assertEqual(r["_cols"], 16)

    def test_17_columns_with_remark(self):
        rows, _ = txt_reader.parse_text(ROW17)
        self.assertEqual(rows[0]["비고"], "재측정 필요，확인")
        self.assertEqual(rows[0]["Lot_ID"], "LOT1")
        self.assertEqual(rows[0]["_cols"], 17)

    def test_mixed_and_quarantine_keep_line_numbers(self):
        text = "\n".join([ROW16, "", "EM,1,2,3", ROW17, "a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r"])
        rows, q = txt_reader.parse_text(text)
        self.assertEqual([r["_line"] for r in rows], [1, 4])
        self.assertEqual([z["line"] for z in q], [3, 5])
        self.assertEqual(q[0]["raw"], "EM,1,2,3")

    def test_header_line_skipped(self):
        rows, q = txt_reader.parse_text("Test,Sdm_no,List_no,Process\n" + ROW16)
        self.assertEqual(len(rows), 1)
        self.assertEqual(q, [])

    def test_encodings(self):
        for enc in ("utf-8", "utf-8-sig", "cp949"):
            text, used = txt_reader.decode_bytes(ROW17.replace("，", ",").encode(enc) if enc == "cp949" else ROW17.encode(enc))
            self.assertIn("재측정", text, enc)
        _, used = txt_reader.decode_bytes("재측정".encode("cp949"))
        self.assertEqual(used, "cp949")

    def test_reader_never_writes(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "em.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(ROW16 + "\n")
            before = (os.stat(p).st_mtime_ns, _read(p))
            txt_reader.read_file(p)
            self.assertEqual(before, (os.stat(p).st_mtime_ns, _read(p)))
        finally:
            shutil.rmtree(d)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.txt = os.path.join(cls.dir, "em.txt")
        with open(cls.txt, "w", encoding="utf-8") as f:
            f.write(ROW16 + "\n")
        cfg_path = os.path.join(cls.dir, "config.ini")
        # 메모장처럼 BOM 을 붙여 저장한 설정도 읽혀야 한다
        with open(cfg_path, "w", encoding="utf-8-sig") as f:
            f.write("[server]\nport = 18780\n[data]\nem_txt = %s\npoll_seconds = 5\nrow_key = Test, List_no\n[tester_ovens]\nOIH_H = 24\nBAD = x\n" % cls.txt)
        cls.cfg = server.load_config(cfg_path)
        cls.cache = server.DataCache(cls.cfg["em_txt"])
        cls.store = store_mod.Store(os.path.join(cls.dir, "overlay.db"))
        cls.httpd, cls.port = server.create_server(cls.cfg, cls.cache, cls.store)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.close()
        shutil.rmtree(cls.dir)

    def get(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def test_config_bom_and_values(self):
        self.assertEqual(self.cfg["ovens"], {"OIH_H": 24})
        self.assertEqual(self.cfg["key_fields"], ["Test", "List_no"])
        self.assertEqual(self.cfg["poll_seconds"], 5)

    def test_data_endpoint(self):
        _, ctype, body = self.get("/api/data")
        self.assertIn("application/json", ctype)
        d = json.loads(body)
        self.assertEqual(len(d["rows"]), 1)
        self.assertEqual(d["config"]["ovens"], {"OIH_H": 24})
        self.assertEqual(d["config"]["key_fields"], ["Test", "List_no"])
        self.assertFalse(d["meta"]["demo"])

    def test_external_append_is_picked_up(self):
        # [위험] 외부 스크립트가 줄을 추가하면 다음 조회에 반영되고 revision 이 올라가야 화면이 갱신된다
        _, _, b1 = self.get("/api/status")
        rev1 = json.loads(b1)["revision"]
        time.sleep(0.05)
        with open(self.txt, "a", encoding="utf-8") as f:
            f.write(ROW17 + "\n")
        self.cache.last_check = 0
        _, _, b2 = self.get("/api/status")
        s2 = json.loads(b2)
        self.assertGreater(s2["revision"], rev1)
        self.assertEqual(s2["rows"], 2)

    def test_static_mime(self):
        for path, mime in (("/", "text/html"), ("/app.js", "application/javascript"), ("/rules.js", "application/javascript"), ("/style.css", "text/css")):
            status, ctype, _ = self.get(path)
            self.assertEqual(status, 200)
            self.assertIn(mime, ctype, path)

    def test_path_traversal_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/../app/server.py")
        self.assertEqual(cm.exception.code, 404)

    def test_write_methods_rejected(self):
        # [위험] 읽기 전용 서버가 어떤 경로로도 원본을 바꾸면 안 된다
        req = urllib.request.Request("http://127.0.0.1:%d/api/data" % self.port, data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 405)

    def post(self, obj):
        req = urllib.request.Request("http://127.0.0.1:%d/api/edit" % self.port, data=json.dumps(obj).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def row293(self):
        d = json.loads(self.get("/api/data")[2])
        return [r for r in d["rows"] if r["List_no"] == "293"][0]

    def test_edit_flow_conflict_and_source_untouched(self):
        # [위험] 두 사람이 같은 칸을 동시에 고치면 나중 사람이 앞사람 값을 모르고 덮어쓰면 안 된다
        before = _read(self.txt)
        r = self.row293()
        self.assertEqual(r["_key"], "EM|293")
        v0 = r["_ver"].get("PKG_in", 0)
        st, body = self.post({"key": "EM|293", "field": "PKG_in", "value": "2024/04/10", "version": v0, "user": "김A"})
        self.assertEqual(st, 200, body)
        self.assertEqual(body["state"]["value"], "2024-04-10")  # 날짜 형식 통일
        st, body = self.post({"key": "EM|293", "field": "PKG_in", "value": "2024-04-11", "version": v0, "user": "이B"})
        self.assertEqual(st, 409)
        self.assertEqual(body["current"]["value"], "2024-04-10")
        self.assertEqual(body["current"]["by"], "김A")
        st, _ = self.post({"key": "EM|293", "field": "PKG_in", "value": "2024-04-11", "version": body["current"]["version"], "user": "이B"})
        self.assertEqual(st, 200)
        r = self.row293()
        self.assertEqual(r["PKG_in"], "2024-04-11")
        self.assertEqual(r["_src"]["PKG_in"], "")  # 원본은 빈 값
        # [위험] 입력이 원본 .txt 를 바꾸면 외부 스크립트가 추가한 줄과 경합한다. 원본은 바이트 그대로여야 한다
        self.assertEqual(_read(self.txt), before)
        hist = json.loads(self.get("/api/history")[2])["items"]
        self.assertEqual([h["user"] for h in hist[:2]], ["이B", "김A"])

    def test_edit_rejections(self):
        cases = [
            ({"key": "EM|293", "field": "Process", "value": "X", "version": 0, "user": "a"}, 400),   # 입력 칸 아님
            ({"key": "EM|293", "field": "Test_in", "value": "2024-02-30", "version": 0, "user": "a"}, 400),  # 없는 날짜
            ({"key": "EM|293", "field": "Test_in", "value": "2024-04-01", "version": 0, "user": ""}, 400),  # 이름 없음
            ({"key": "EM|999", "field": "Test_in", "value": "2024-04-01", "version": 0, "user": "a"}, 404),  # 없는 행
        ]
        for req, code in cases:
            st, body = self.post(req)
            self.assertEqual(st, code, (req, body))

    def test_export_txt_merges_and_keeps_format(self):
        self.post({"key": "EM|293", "field": "비고", "value": "재측정, 필요", "version": self.row293()["_ver"].get("비고", 0), "user": "a"})
        _, ctype, body = self.get("/api/export/txt")
        line = [l for l in body.decode("utf-8").splitlines() if ",293," in l][0]
        parts = line.split(",")
        self.assertEqual(len(parts), 17)  # 쉼표가 전각으로 바뀌어 컬럼이 밀리지 않음
        self.assertEqual(parts[14], "재측정， 필요")
        self.assertEqual(parts[4], ".")

    def save(self, ops, user="김A"):
        req = urllib.request.Request("http://127.0.0.1:%d/api/save" % self.port,
                                     data=json.dumps({"user": user, "ops": ops}).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_save_add_delete_and_export(self):
        before = _read(self.txt)
        st, body = self.save([{"op": "add", "row": {"List_no": "777", "Process": "LN014LPP", "DUT": "4/4"}},
                              {"op": "delete", "key": "EM|293"}])
        self.assertEqual(st, 200, body)
        d = json.loads(self.get("/api/data")[2])
        keys = [r["_key"] for r in d["rows"]]
        self.assertIn("EM|777", keys)
        self.assertNotIn("EM|293", keys)
        self.assertEqual([r["_key"] for r in d["deleted"]], ["EM|293"])
        txt = self.get("/api/export/txt")[2].decode("utf-8")
        self.assertIn(",777,LN014LPP,", txt)
        self.assertNotIn(",293,", txt)
        self.assertEqual(_read(self.txt), before)  # 원본은 그대로
        st, _ = self.save([{"op": "restore", "key": "EM|293"}, {"op": "delete", "key": "EM|777"}])
        self.assertEqual(st, 200)
        st, body = self.save([{"op": "add", "row": {"List_no": "293", "Process": "X"}}])
        self.assertEqual(st, 400)
        self.assertIn("이미 있는", body["error"])

    def test_save_conflict_returns_list(self):
        v = self.row293()["_ver"].get("Test_out", 0)
        self.assertEqual(self.save([{"op": "set", "key": "EM|293", "field": "Test_out", "value": "2024-06-01", "version": v}])[0], 200)
        st, body = self.save([{"op": "set", "key": "EM|293", "field": "Test_out", "value": "2024-06-02", "version": v}], user="이B")
        self.assertEqual(st, 409)
        self.assertEqual(body["conflicts"][0]["current"]["value"], "2024-06-01")
        self.assertEqual(body["conflicts"][0]["mine"], "2024-06-02")

    def test_missing_file_keeps_last_data(self):
        cache = server.DataCache(self.txt)
        cache.refresh(min_interval=0)
        cache.path = os.path.join(self.dir, "없음.txt")
        cache.refresh(min_interval=0)
        self.assertIn("찾을 수 없습니다", cache.error)
        self.assertGreaterEqual(len(cache.data["rows"]), 1)


if __name__ == "__main__":
    unittest.main()

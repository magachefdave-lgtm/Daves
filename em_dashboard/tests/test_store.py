"""입력 저장소 단위 테스트."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import store as S  # noqa: E402


def row(**kw):
    base = {c: "" for c in ["Test", "Sdm_no", "List_no", "Process", "Test_in", "Test_out", "DUT", "Total수량", "Wafer_in",
                            "PKG_Line", "PKG_in", "Vehicle", "Tester", "Engineer", "비고", "Lot_ID", "Structure"]}
    base.update(Test="EM", List_no="1")
    base.update(kw)
    return base


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = S.Store(os.path.join(self.dir, "o.db"))

    def tearDown(self):
        self.s.close()
        shutil.rmtree(self.dir)
        S.set_key_fields(S.DEFAULT_KEY_FIELDS)

    def test_clean_value(self):
        self.assertEqual(S.clean_value("Test_in", "2026.1.5"), "2026-01-05")
        self.assertEqual(S.clean_value("Test_in", " . "), "")
        self.assertEqual(S.clean_value("비고", "a,b\nc"), "a，b c")
        for bad in ("2026-13-01", "27/11/2026", "어제"):
            with self.assertRaises(ValueError):
                S.clean_value("Test_out", bad)
        with self.assertRaises(ValueError):
            S.clean_value("Engineer", "X")

    def test_version_conflict(self):
        ok, st = self.s.set("EM|1", "Test_in", "2026-11-01", 0, "A", "ip", "")
        self.assertTrue(ok)
        self.assertEqual(st["version"], 1)
        ok, st = self.s.set("EM|1", "Test_in", "2026-11-02", 0, "B", "ip", "")
        self.assertFalse(ok)
        self.assertEqual((st["value"], st["by"]), ("2026-11-01", "A"))
        self.assertEqual(self.s.counter, 1)

    def test_same_value_is_noop(self):
        ok, st = self.s.set("EM|1", "Sdm_no", "2838", 0, "A", "ip", "2838")
        self.assertTrue(ok)
        self.assertEqual(st["version"], 0)
        self.assertEqual(self.s.history(), [])

    def test_merge_overlay_wins_and_marks_source(self):
        self.s.set("EM|1", "PKG_in", "2026-11-03", 0, "A", "ip", "")
        self.s.set("EM|1", "Wafer_in", "", 0, "A", "ip", "2026-10-01")  # 지움
        m = S.merge([row(Wafer_in="2026-10-01")], self.s.all())[0][0]
        self.assertEqual(m["PKG_in"], "2026-11-03")
        self.assertEqual(m["Wafer_in"], "")
        self.assertEqual(m["_src"], {"PKG_in": "", "Wafer_in": "2026-10-01"})
        self.assertEqual(m["_ver"], {"PKG_in": 1, "Wafer_in": 1})

    def test_merge_duplicate_key_not_applied(self):
        # [위험] List_no 가 겹치는 두 줄에 같은 입력값이 덮이면 엉뚱한 건의 날짜가 바뀐다
        self.s.set("EM|1", "PKG_in", "2026-11-03", 0, "A", "ip", "")
        out, _ = S.merge([row(), row()], self.s.all())
        self.assertTrue(all(r["_dup"] for r in out))
        self.assertTrue(all(r["PKG_in"] == "" for r in out))

    def test_merge_does_not_mutate_source_rows(self):
        src = [row()]
        self.s.set("EM|1", "Test_in", "2026-11-03", 0, "A", "ip", "")
        S.merge(src, self.s.all())
        self.assertEqual(src[0]["Test_in"], "")

    def test_to_txt_roundtrip(self):
        from app import txt_reader
        r = row(Sdm_no="2838", List_no="293", Process="P", DUT="8/8", **{"Total수량": "128", "비고": "x，y", "Lot_ID": "L1|L2"})
        text = S.to_txt([r], [{"raw": "broken,line"}])
        rows, q = txt_reader.parse_text(text)
        self.assertEqual(rows[0]["Lot_ID"], "L1|L2")
        self.assertEqual(rows[0]["Test_in"], "")
        self.assertEqual(rows[0]["비고"], "x，y")
        self.assertEqual(q[0]["raw"], "broken,line")

    # ---------- 일괄 저장 (저장 버튼) ----------
    def test_batch_all_or_nothing_on_conflict(self):
        # [위험] 묶음 일부만 저장되면 화면과 서버가 어긋난다. 겹친 칸이 하나라도 있으면 아무것도 쓰지 않는다
        src = [row(List_no="1"), row(List_no="2")]
        self.s.set("EM|1", "Test_in", "2026-11-01", 0, "A", "ip", "")
        st, payload = self.s.apply_batch([
            {"op": "set", "key": "EM|2", "field": "PKG_in", "value": "2026-11-05", "version": 0},
            {"op": "set", "key": "EM|1", "field": "Test_in", "value": "2026-11-09", "version": 0},
        ], "B", "ip", src)
        self.assertEqual(st, "conflict")
        self.assertEqual([(c["i"], c["key"], c["current"]["value"], c["current"]["by"]) for c in payload],
                         [(1, "EM|1", "2026-11-01", "A")])
        self.assertNotIn("EM|2", self.s.all())

    def test_batch_add_edit_delete_restore(self):
        src = [row(List_no="1")]
        st, p = self.s.apply_batch([
            {"op": "add", "row": {"List_no": "9", "Process": "LN020LEP", "DUT": "8/8", "Lot_ID": "A|B", "비고": "새, 시료"}},
            {"op": "delete", "key": "EM|1"},
        ], "A", "ip", src)
        self.assertEqual((st, p["applied"]), ("ok", 2))
        e, added, deleted = self.s.snapshot()
        vis, gone = S.merge(src, e, added, deleted)
        self.assertEqual([r["_key"] for r in vis], ["EM|9"])
        self.assertEqual([r["_key"] for r in gone], ["EM|1"])
        self.assertEqual(vis[0]["Total수량"], "128")  # DUT 8 x 16 자동
        self.assertEqual(vis[0]["Test"], "EM")
        self.assertEqual(vis[0]["비고"], "새， 시료")
        self.assertTrue(vis[0]["_added"])
        # 추가한 행의 입력 칸 수정
        st, _ = self.s.apply_batch([{"op": "set", "key": "EM|9", "field": "PKG_in", "value": "2026-11-20", "version": 0}], "B", "ip", src)
        self.assertEqual(st, "ok")
        st, _ = self.s.apply_batch([{"op": "restore", "key": "EM|1"}], "B", "ip", src)
        vis, gone = S.merge(src, *self.s.snapshot())
        self.assertEqual(sorted(r["_key"] for r in vis), ["EM|1", "EM|9"])
        self.assertEqual([r["PKG_in"] for r in vis if r["_key"] == "EM|9"], ["2026-11-20"])
        self.assertEqual(gone, [])
        fields = [h["field"] for h in self.s.history()]
        self.assertIn("(행 추가)", fields)
        self.assertIn("(행 삭제)", fields)
        self.assertIn("(행 되살림)", fields)

    def test_batch_rejections(self):
        src = [row(List_no="1"), row(List_no="5"), row(List_no="5")]
        bad = [
            [{"op": "add", "row": {"List_no": "1", "Process": "P"}}],          # 원본에 있는 키
            [{"op": "add", "row": {"List_no": "", "Process": "P"}}],           # List_no 없음
            [{"op": "add", "row": {"List_no": "7"}}],                         # Process 없음
            [{"op": "add", "row": {"List_no": "7", "Process": "P"}}, {"op": "add", "row": {"List_no": "7", "Process": "P"}}],
            [{"op": "add", "row": {"List_no": "7", "Process": "P", "Test_in": "2026-02-30"}}],
            [{"op": "delete", "key": "EM|5"}],                                 # 중복 키
            [{"op": "delete", "key": "EM|404"}],                               # 없는 행
            [{"op": "set", "key": "EM|1", "field": "Process", "value": "X", "version": 0}],
            [{"op": "drop_table"}],
            [],
        ]
        for ops in bad:
            with self.assertRaises(S.BatchError, msg=str(ops)):
                self.s.apply_batch(ops, "A", "ip", src)
        e, added, deleted = self.s.snapshot()
        self.assertEqual((e, added, deleted), ({}, [], {}))

    def test_readd_after_delete_starts_clean(self):
        src = []
        self.s.apply_batch([{"op": "add", "row": {"List_no": "3", "Process": "P"}}], "A", "ip", src)
        self.s.apply_batch([{"op": "set", "key": "EM|3", "field": "비고", "value": "old", "version": 0}], "A", "ip", src)
        self.s.apply_batch([{"op": "delete", "key": "EM|3"}], "A", "ip", src)
        st, _ = self.s.apply_batch([{"op": "add", "row": {"List_no": "3", "Process": "Q"}}], "B", "ip", src)
        self.assertEqual(st, "ok")
        vis, gone = S.merge(src, *self.s.snapshot())
        self.assertEqual((vis[0]["Process"], vis[0]["비고"], gone), ("Q", "", []))

    def test_ten_users_concurrent_batches(self):
        # [위험] 2~10명이 동시에 [저장]을 눌러도 저장이 빠지거나 DB 가 깨지면 안 된다
        import threading
        src = [row(List_no=str(i)) for i in range(10)]
        results = []

        def worker(i):
            ops = [{"op": "set", "key": "EM|%d" % i, "field": "Test_in", "value": "2026-11-%02d" % (i + 1), "version": 0},
                   {"op": "add", "row": {"List_no": "1%02d" % i, "Process": "P"}}]
            results.append(self.s.apply_batch(ops, "U%d" % i, "ip", src)[0])
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results, ["ok"] * 10)
        vis, _ = S.merge(src, *self.s.snapshot())
        self.assertEqual(len(vis), 20)
        self.assertEqual(sorted(r["Test_in"] for r in vis if r["Test_in"]), ["2026-11-%02d" % (i + 1) for i in range(10)])

    def test_backup_keeps_recent(self):
        folder = os.path.join(self.dir, "b")
        os.makedirs(folder)
        for i in range(20):
            open(os.path.join(folder, "overlay_2025%04d.db" % i), "w").close()
        self.s.backup(folder, keep=5)
        self.assertEqual(len(os.listdir(folder)), 5)


    def test_parse_key_fields(self):
        self.assertEqual(S.parse_key_fields(""), ["Test", "List_no"])
        self.assertEqual(S.parse_key_fields(" Test , List_no , Lot_ID "), ["Test", "List_no", "Lot_ID"])
        for bad in ("Test,Nope", "Test,Sdm_no", "Test,List_no,List_no", "Test"):
            with self.assertRaises(ValueError):
                S.parse_key_fields(bad)

    def test_key_change_remaps_saved_records(self):
        src = [row(List_no="1", Lot_ID="L1"), row(List_no="2", Lot_ID="L2"), row(List_no="3", Lot_ID="L3")]
        self.assertIsNone(self.s.remap_keys(src))  # 첫 시작: 기준만 기록
        self.s.apply_batch([
            {"op": "set", "key": "EM|1", "field": "Sdm_no", "value": "S1", "version": 0},
            {"op": "delete", "key": "EM|2"},
            {"op": "add", "row": {"List_no": "9", "Process": "P", "Lot_ID": "L9"}},
            {"op": "set", "key": "EM|9", "field": "비고", "value": "n", "version": 0},
        ], "A", "ip", src)
        S.set_key_fields(["Test", "List_no", "Lot_ID"])
        self.assertEqual(self.s.remap_keys(src), (3, 0))
        vis, gone = S.merge(src, *self.s.snapshot())
        by = {r["_key"]: r for r in vis}
        self.assertEqual(by["EM|1|L1"]["Sdm_no"], "S1")
        self.assertEqual(by["EM|9|L9"]["비고"], "n")
        self.assertEqual([r["_key"] for r in gone], ["EM|2|L2"])
        self.assertTrue(all(h["key"].count("|") == 2 for h in self.s.history()))
        self.assertIsNone(self.s.remap_keys(src))  # 두 번째는 변경 없음
        # 새 기준으로 저장도 된다
        st, _ = self.s.apply_batch([{"op": "set", "key": "EM|3|L3", "field": "Sdm_no", "value": "S3", "version": 0}], "B", "ip", src)
        self.assertEqual(st, "ok")

    def test_key_change_skips_ambiguous_rows(self):
        src = [row(List_no="1", Lot_ID="A"), row(List_no="1", Lot_ID="B"), row(List_no="2", Lot_ID="C")]
        self.s.remap_keys(src)
        self.s.apply_batch([{"op": "set", "key": "EM|2", "field": "Sdm_no", "value": "S2", "version": 0}], "A", "ip", src)
        # 옛 기준에서 중복이던 EM|1 에 직접 넣어 둔 기록 (못 옮기는 경우)
        self.s.conn.execute("INSERT INTO edits VALUES('EM|1','Sdm_no','X',1,'A','t')"); self.s.conn.commit()
        S.set_key_fields(["Test", "List_no", "Lot_ID"])
        self.assertEqual(self.s.remap_keys(src), (1, 1))
        vis, _ = S.merge(src, *self.s.snapshot())
        self.assertEqual([r["Sdm_no"] for r in vis if r["_key"] == "EM|2|C"], ["S2"])
        self.assertFalse(any(r["_dup"] for r in vis))  # 새 기준에서는 중복 아님

    def test_equipment_column_is_overlay_only(self):
        src = [row(List_no="1")]
        st, _ = self.s.apply_batch([
            {"op": "set", "key": "EM|1", "field": "설비", "value": "MO-01", "version": 0},
            {"op": "add", "row": {"List_no": "9", "Process": "P", "설비": "MO-02"}},
        ], "A", "ip", src)
        self.assertEqual(st, "ok")
        vis, _ = S.merge(src, *self.s.snapshot())
        self.assertEqual({r["_key"]: r["설비"] for r in vis}, {"EM|1": "MO-01", "EM|9": "MO-02"})
        self.assertNotIn("설비", src[0])  # 원본 행은 그대로
        lines = S.to_txt(vis, []).split("\r\n")
        self.assertTrue(all(l.count(",") == 16 for l in lines if l))  # 내보내기는 원본과 같은 17컬럼
        self.assertNotIn("MO-0", S.to_txt(vis, []))

if __name__ == "__main__":
    unittest.main()

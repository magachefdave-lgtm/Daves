"""작업자 입력 저장소 (서버 PC 로컬 SQLite, 표준 라이브러리 sqlite3).

원본 .txt 는 외부 스크립트가 계속 줄을 추가하는 파일이라 서버가 고쳐 쓰지 않는다.
작업자가 표에서 바꾼 것은 모두 여기에만 저장하고, 화면에 보낼 때 원본 위에 덮어 보여준다.

저장하는 것 세 가지:
- edits   : 칸 입력값 (Sdm_no, Test_in, Test_out, Wafer_in, PKG_Line, PKG_in, 비고). 칸마다 version
- added   : 이 프로그램에서 추가한 행 (17컬럼 전체)
- deleted : 삭제 표시 (원본 행이든 추가 행이든 화면과 내보내기에서 빠짐, 되살리기 가능)

저장 방식: 화면은 바꾼 것을 모아 두었다가 [저장] 때 한 번에 보낸다(apply_batch).
  한 묶음은 전부 반영되거나 전부 반영되지 않는다. 다른 사람이 먼저 바꾼 칸이 있으면
  아무것도 반영하지 않고 겹친 칸 목록(409)을 돌려준다.

행 식별 키: 기본 Test + List_no (config.ini [data] row_key 로 바꿀 수 있음).
같은 키가 두 줄 이상이거나 키 칸이 비면 그 행들은 입력·삭제를 막는다.
키 설정을 바꾸면 서버 시작 때 remap_keys 가 저장된 입력값을 새 키로 옮긴다.
"""
import datetime as dt
import json
import os
import re
import sqlite3
import threading

COLUMNS_17 = [
    "Test", "Sdm_no", "List_no", "Process", "Test_in", "Test_out", "DUT", "Total수량",
    "Wafer_in", "PKG_Line", "PKG_in", "Vehicle", "Tester", "Engineer", "비고", "Lot_ID", "Structure",
]
# 원본 .txt 에 없는 칸: 이 프로그램에서만 입력·저장
# 설비 = Micro oven 설비명, Oven = 그 평가가 들어간 Oven(DUT) 번호 (예: 1|2|3 또는 1-8)
EXTRA_COLUMNS = ["설비", "Oven"]
EDITABLE = ["Sdm_no", "Test_in", "Test_out", "Wafer_in", "PKG_Line", "PKG_in", "비고", "설비", "Oven"]
DATE_FIELDS = {"Test_in", "Test_out", "Wafer_in", "PKG_Line", "PKG_in"}
MAX_LEN = 200
MAX_OPS = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS edits (
  row_key TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
  version INTEGER NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (row_key, field)
);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, row_key TEXT NOT NULL, field TEXT NOT NULL,
  old_value TEXT, new_value TEXT NOT NULL, user_name TEXT NOT NULL, ip TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS added (
  row_key TEXT PRIMARY KEY, data TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deleted (
  row_key TEXT PRIMARY KEY, deleted_by TEXT NOT NULL, deleted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  name TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


class BatchError(ValueError):
    """묶음 안 i 번째 작업이 잘못됨 (400)."""

    def __init__(self, i, msg):
        ValueError.__init__(self, msg)
        self.i = i


DEFAULT_KEY_FIELDS = ["Test", "List_no"]
KEY_FIELDS = list(DEFAULT_KEY_FIELDS)


def parse_key_fields(text):
    """config 의 row_key 값("Test,List_no") 검사. 문제가 있으면 ValueError(한국어)."""
    fields = [f.strip() for f in str(text or "").split(",") if f.strip()]
    if not fields:
        return list(DEFAULT_KEY_FIELDS)
    for f in fields:
        if f not in COLUMNS_17:
            raise ValueError("row_key 에 없는 컬럼 이름이 있습니다: %s" % f)
        if f in EDITABLE:
            raise ValueError("row_key 에 작업자 입력 칸(%s)은 쓸 수 없습니다. 입력할 때마다 키가 바뀌기 때문입니다." % f)
    if len(set(fields)) != len(fields):
        raise ValueError("row_key 에 같은 컬럼이 두 번 있습니다.")
    if fields == ["Test"]:
        raise ValueError("row_key 는 Test 하나만으로는 행을 구분할 수 없습니다.")
    return fields


def set_key_fields(fields):
    KEY_FIELDS[:] = fields


def row_key(r, fields=None):
    return "|".join(str(r.get(f, "") or "") for f in (fields or KEY_FIELDS))


def key_missing(r):
    """키 칸이 비어 행을 구분할 수 없음 (Test 는 비어도 됨: 새 행은 EM 으로 채움)."""
    return any(not r.get(f) for f in KEY_FIELDS if f != "Test")


def _text(v):
    v = "" if v is None else str(v)
    v = v.replace("\r", " ").replace("\n", " ").strip()
    v = v.replace(",", "，")  # 원본 형식으로 내보낼 때 컬럼이 밀리지 않게 (v1.2 와 같은 규칙)
    if v == ".":
        v = ""
    if len(v) > MAX_LEN:
        raise ValueError("%d자 이하로 입력하세요." % MAX_LEN)
    return v


def _date(v, field):
    if not v:
        return v
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", v)
    if not m:
        raise ValueError("%s: 날짜는 2026-11-27 형식으로 입력하세요." % field)
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        raise ValueError("%s: 없는 날짜입니다 (%s)" % (field, v))


def clean_value(field, value):
    """입력 칸 값 정리. 문제가 있으면 ValueError(한국어 메시지)."""
    if field not in EDITABLE:
        raise ValueError("입력할 수 없는 칸입니다: %s" % field)
    v = _text(value)
    return _date(v, field) if field in DATE_FIELDS else v


def clean_row(row):
    """새 행 17칸 정리. Test 기본값 EM, List_no·Process 필수, Total수량 비면 DUT x 16."""
    out = {}
    for c in COLUMNS_17 + EXTRA_COLUMNS:
        v = _text((row or {}).get(c, ""))
        out[c] = _date(v, c) if c in DATE_FIELDS else v
    out["Test"] = out["Test"] or "EM"
    for f in ["List_no", "Process"] + [k for k in KEY_FIELDS if k not in ("List_no", "Process", "Test")]:
        if not out[f]:
            raise ValueError("%s 는 꼭 입력해야 합니다." % f)
    for f in KEY_FIELDS:
        if "|" in out[f]:
            raise ValueError("%s 에는 | 를 쓸 수 없습니다 (행 구분 키)." % f)
    if not out["Total수량"]:
        m = re.match(r"^\s*(\d+)", out["DUT"])
        if m:
            out["Total수량"] = str(int(m.group(1)) * 16)
    if out["Total수량"] and not out["Total수량"].isdigit():
        raise ValueError("Total수량 은 숫자로 입력하세요.")
    return out


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")  # 로컬 디스크 전제. 공유 폴더에 두지 않는다
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.counter = 0  # 변경이 생길 때마다 올라감 (화면 자동 갱신용)

    def close(self):
        self.conn.close()

    # ---------- 읽기 ----------
    def _edits(self):
        out = {}
        for k, f, v, ver, by, at in self.conn.execute(
                "SELECT row_key, field, value, version, updated_by, updated_at FROM edits"):
            out.setdefault(k, {})[f] = {"value": v, "version": ver, "by": by, "at": at}
        return out

    def _added(self):
        out = []
        for k, data, by, at in self.conn.execute(
                "SELECT row_key, data, created_by, created_at FROM added ORDER BY created_at, row_key"):
            r = json.loads(data)
            r["_added_by"] = "%s %s" % (by, at)
            out.append(r)
        return out

    def _deleted(self):
        return {k: {"by": by, "at": at} for k, by, at in self.conn.execute(
            "SELECT row_key, deleted_by, deleted_at FROM deleted")}

    def all(self):
        """칸 입력값만 {row_key: {field: {value, version, by, at}}} (이전 버전 호환)."""
        with self.lock:
            return self._edits()

    def snapshot(self):
        """한 시점의 edits, added, deleted 를 함께 읽는다."""
        with self.lock:
            return self._edits(), self._added(), self._deleted()

    def history(self, limit=300):
        with self.lock:
            cur = self.conn.execute(
                "SELECT row_key, field, old_value, new_value, user_name, ip, at FROM history ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(zip(("key", "field", "old", "new", "user", "ip", "at"), r)) for r in cur]

    # ---------- 쓰기 ----------
    def _log(self, key, field, old, new, user, ip, at):
        self.conn.execute(
            "INSERT INTO history(row_key, field, old_value, new_value, user_name, ip, at) VALUES(?,?,?,?,?,?,?)",
            (key, field, old, new, user, ip, at))

    def set(self, key, field, value, expected_version, user, ip, original):
        """칸 하나 저장 (/api/edit). (True, 새 상태) 또는 충돌 시 (False, 현재 상태)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT value, version, updated_by, updated_at FROM edits WHERE row_key=? AND field=?", (key, field)).fetchone()
            cur_ver = row[1] if row else 0
            current = {"value": row[0] if row else original, "version": cur_ver,
                       "by": row[2] if row else None, "at": row[3] if row else None}
            if int(expected_version) != cur_ver:
                return False, current
            if current["value"] == value:
                return True, current
            at = now()
            self._upsert_edit(key, field, value, cur_ver + 1, user, at)
            self._log(key, field, current["value"], value, user, ip, at)
            self.conn.commit()
            self.counter += 1
            return True, {"value": value, "version": cur_ver + 1, "by": user, "at": at}

    def _upsert_edit(self, key, field, value, version, user, at):
        self.conn.execute(
            "INSERT INTO edits(row_key, field, value, version, updated_by, updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(row_key, field) DO UPDATE SET value=excluded.value, version=excluded.version, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (key, field, value, version, user, at))

    def apply_batch(self, ops, user, ip, source_rows):
        """[저장] 한 번의 묶음. 전부 반영하거나 전부 반영하지 않는다.

        ops 항목:
          {"op": "set", "key", "field", "value", "version"}   칸 입력 (version = 화면이 본 값)
          {"op": "add", "row": {17칸}}                         행 추가
          {"op": "delete", "key"}                              삭제 표시
          {"op": "restore", "key"}                             되살리기
        반환: ("ok", {"applied": n}) / ("conflict", [ {i, key, field, current} ... ])
        잘못된 요청은 BatchError (400).
        """
        if not isinstance(ops, list) or not ops:
            raise BatchError(-1, "저장할 변경이 없습니다.")
        if len(ops) > MAX_OPS:
            raise BatchError(-1, "한 번에 %d건까지 저장할 수 있습니다." % MAX_OPS)
        with self.lock:
            edits, added_list, deleted = self._edits(), self._added(), self._deleted()
            src_count = {}
            for r in source_rows:
                k = row_key(r)
                src_count[k] = src_count.get(k, 0) + 1
            src_by_key = {row_key(r): r for r in source_rows}
            added = {row_key(r): r for r in added_list}

            # 1) 검사만 (아무것도 쓰지 않음)
            plan, conflicts = [], []
            new_keys = set()
            for i, op in enumerate(ops):
                kind = (op or {}).get("op")
                try:
                    if kind == "add":
                        row = clean_row(op.get("row"))
                        k = row_key(row)
                        if k in src_count or (k in added and k not in deleted) or k in new_keys:
                            raise ValueError("이미 있는 행입니다 (%s = %s)" % ("+".join(KEY_FIELDS), k))
                        new_keys.add(k)
                        plan.append(("add", k, row))
                    elif kind in ("set", "delete", "restore"):
                        k = str(op.get("key") or "")
                        exists = k in src_count or k in added or k in new_keys
                        if not exists:
                            raise ValueError("행을 찾을 수 없습니다: %s (새로 읽은 뒤 다시 시도하세요)" % k)
                        if src_count.get(k, 0) + (1 if k in added else 0) > 1:
                            raise ValueError("행 구분 키가 중복된 행은 바꿀 수 없습니다: %s" % k)
                        if kind == "set":
                            f = op.get("field")
                            v = clean_value(f, op.get("value"))
                            base = (src_by_key.get(k) or added.get(k) or {}).get(f, "")
                            cur = edits.get(k, {}).get(f)
                            cur_ver = cur["version"] if cur else 0
                            if k not in new_keys and int(op.get("version", 0)) != cur_ver:
                                conflicts.append({"i": i, "key": k, "field": f, "mine": v, "current": {
                                    "value": cur["value"] if cur else base, "version": cur_ver,
                                    "by": cur["by"] if cur else None, "at": cur["at"] if cur else None}})
                                continue
                            plan.append(("set", k, (f, v, cur["value"] if cur else base, cur_ver)))
                        else:
                            plan.append((kind, k, None))
                    else:
                        raise ValueError("알 수 없는 작업입니다: %s" % kind)
                except ValueError as e:
                    if isinstance(e, BatchError):
                        raise
                    raise BatchError(i, str(e))
            if conflicts:
                return "conflict", conflicts

            # 2) 한 트랜잭션으로 반영
            at, n = now(), 0
            try:
                for kind, k, arg in plan:
                    if kind == "add":
                        # 예전에 추가했다가 지운 같은 키가 있으면 새 행으로 바꾼다 (이전 입력값도 비움)
                        self.conn.execute("DELETE FROM edits WHERE row_key=?", (k,))
                        self.conn.execute("DELETE FROM deleted WHERE row_key=?", (k,))
                        self.conn.execute(
                            "INSERT OR REPLACE INTO added(row_key, data, created_by, created_at) VALUES(?,?,?,?)",
                            (k, json.dumps(arg, ensure_ascii=False), user, at))
                        self._log(k, "(행 추가)", None, ",".join(arg[c] or "." for c in COLUMNS_17), user, ip, at)
                        n += 1
                    elif kind == "set":
                        f, v, old, cur_ver = arg
                        if v == old:
                            continue
                        self._upsert_edit(k, f, v, cur_ver + 1, user, at)
                        self._log(k, f, old, v, user, ip, at)
                        n += 1
                    elif kind == "delete":
                        if self.conn.execute("SELECT 1 FROM deleted WHERE row_key=?", (k,)).fetchone():
                            continue
                        self.conn.execute("INSERT INTO deleted(row_key, deleted_by, deleted_at) VALUES(?,?,?)", (k, user, at))
                        self._log(k, "(행 삭제)", None, "삭제", user, ip, at)
                        n += 1
                    elif kind == "restore":
                        cur = self.conn.execute("DELETE FROM deleted WHERE row_key=?", (k,))
                        if cur.rowcount:
                            self._log(k, "(행 되살림)", None, "되살림", user, ip, at)
                            n += 1
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            if n:
                self.counter += 1
            return "ok", {"applied": n}

    def remap_keys(self, source_rows):
        """row_key 설정이 바뀌었으면 저장된 입력값·추가·삭제·이력을 새 키로 옮긴다.

        옛 키와 새 키가 모두 한 행에만 해당할 때만 옮긴다. 반환: (옮긴 키 수, 못 옮긴 키 수) 또는 None(변경 없음).
        """
        new = ",".join(KEY_FIELDS)
        TMP = "~remap~"
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE name='key_fields'").fetchone()
            if row is None or row[0] == new:
                self.conn.execute("INSERT OR REPLACE INTO meta(name, value) VALUES('key_fields', ?)", (new,))
                self.conn.commit()
                return None
            old_fields = row[0].split(",")
            rows = list(source_rows) + [json.loads(d) for (d,) in self.conn.execute("SELECT data FROM added")]
            olds, news = {}, {}
            for r in rows:
                o, n = row_key(r, old_fields), row_key(r, KEY_FIELDS)
                olds.setdefault(o, set()).add(n)
                news[n] = news.get(n, 0) + 1
            mapping = {o: next(iter(ns)) for o, ns in olds.items() if len(ns) == 1 and news[next(iter(ns))] == 1}
            stored = set()
            for t in ("edits", "deleted", "added"):
                stored.update(k for (k,) in self.conn.execute("SELECT DISTINCT row_key FROM %s" % t))
            moved = [k for k in stored if k in mapping]
            try:
                # 새 키가 다른 옛 키와 겹칠 수 있어 임시 이름을 거쳐 두 번에 옮긴다
                for k in moved:
                    for t in ("edits", "deleted", "added", "history"):
                        self.conn.execute("UPDATE %s SET row_key=? WHERE row_key=?" % t, (TMP + mapping[k], k))
                for t in ("edits", "deleted", "added", "history"):
                    self.conn.execute("UPDATE %s SET row_key=substr(row_key, ?) WHERE substr(row_key, 1, ?)=?" % t,
                                      (len(TMP) + 1, len(TMP), TMP))
                self.conn.execute("UPDATE meta SET value=? WHERE name='key_fields'", (new,))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            self.counter += 1
            return len(moved), len(stored) - len(moved)

    def backup(self, folder, keep=14):
        """하루 한 번 스냅샷 (서버 시작 시). 최근 keep 개만 남긴다."""
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "overlay_%s.db" % dt.date.today().strftime("%Y%m%d"))
        with self.lock:
            dst = sqlite3.connect(target)
            self.conn.backup(dst)
            dst.close()
        snaps = sorted(f for f in os.listdir(folder) if f.startswith("overlay_") and f.endswith(".db"))
        for f in snaps[:-keep]:
            os.remove(os.path.join(folder, f))
        return target


def merge(rows, edits, added=None, deleted=None):
    """원본 행 + 추가 행에 입력값을 덮고 삭제 표시를 뺀 목록, 그리고 삭제된 행 목록을 만든다.

    원본 rows 는 바꾸지 않는다. 반환: (보이는 행 목록, 삭제된 행 목록)
    각 행에 붙는 값: _key, _dup(키 중복이면 True, 입력·삭제 불가), _added(이 프로그램에서 추가),
    _ver{field: version}, _src{field: 원본값}(입력값으로 바뀐 칸만), _by{field: "이름 시각"}
    """
    added = added or []
    deleted = deleted or {}
    base = [dict(r) for r in rows] + [dict(r, _added=True, _line=None) for r in added]
    counts = {}
    for r in base:
        k = row_key(r)
        counts[k] = counts.get(k, 0) + 1
    out, gone = [], []
    for r in base:
        m = dict(r)
        k = row_key(r)
        m["_key"] = k
        m["_dup"] = counts[k] > 1 or key_missing(r)
        m.setdefault("_added", False)
        m["_ver"], m["_src"], m["_by"] = {}, {}, {}
        if not m["_dup"]:
            for f, e in edits.get(k, {}).items():
                if f not in EDITABLE:
                    continue
                m["_ver"][f] = e["version"]
                if e["value"] != r.get(f, ""):
                    m["_src"][f] = r.get(f, "")
                m[f] = e["value"]
                m["_by"][f] = "%s %s" % (e["by"], e["at"])
        if k in deleted and not m["_dup"]:
            m["_deleted_by"] = "%s %s" % (deleted[k]["by"], deleted[k]["at"])
            gone.append(m)
        else:
            out.append(m)
    return out, gone


def to_txt(rows, quarantine):
    """합친 결과를 원본과 같은 17컬럼 형식으로 (빈 값은 '.'). 격리 줄은 끝에 원문 그대로."""
    lines = [",".join((r.get(c, "") or ".") for c in COLUMNS_17) for r in rows]
    lines += [q["raw"] for q in quarantine]
    return "\r\n".join(lines) + "\r\n"

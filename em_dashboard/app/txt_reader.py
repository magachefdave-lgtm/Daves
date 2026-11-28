"""EM .txt DB 읽기 (읽기 전용).

이 모듈은 원본 파일을 절대 쓰지 않는다. 외부 스크립트가 같은 파일에 줄을 추가하므로
서버가 파일을 교체하면 그 줄이 사라질 수 있기 때문이다.

형식 (v1.2 문서 기준):
- 쉼표 구분, 빈 값은 '.', 다중 값은 '|'
- 17컬럼(비고 포함)과 16컬럼(비고 없음)이 한 파일에 섞일 수 있다
- 그 외 컬럼 수의 줄은 격리(quarantine)하고 원문을 보존한다
- 인코딩은 UTF-8(BOM 포함) -> CP949 -> EUC-KR 순서로 감지
"""
import hashlib
import os

COLUMNS_17 = [
    "Test", "Sdm_no", "List_no", "Process", "Test_in", "Test_out", "DUT", "Total수량",
    "Wafer_in", "PKG_Line", "PKG_in", "Vehicle", "Tester", "Engineer", "비고", "Lot_ID", "Structure",
]
COLUMNS_16 = [c for c in COLUMNS_17 if c != "비고"]
ENCODINGS = ("utf-8-sig", "cp949", "euc-kr")
EMPTY = "."


def decode_bytes(raw):
    """바이트를 문자열로. (text, encoding) 반환. 모두 실패하면 cp949 replace."""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace"), "cp949(손상 문자 대체)"


def _is_header(parts):
    return len(parts) >= 3 and parts[0].strip().lower() == "test" and parts[2].strip().lower() == "list_no"


def parse_text(text):
    """문자열 전체를 파싱. (rows, quarantine) 반환."""
    rows, quarantine = [], []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [p.strip() for p in stripped.split(",")]
        if _is_header(parts):
            continue
        if len(parts) == 17:
            cols = COLUMNS_17
        elif len(parts) == 16:
            cols = COLUMNS_16
        else:
            quarantine.append({"line": line_no, "raw": line, "reason": "컬럼 수 %d개 (16 또는 17이어야 함)" % len(parts)})
            continue
        rec = {c: ("" if v == EMPTY else v) for c, v in zip(cols, parts)}
        rec.setdefault("비고", "")
        rec["_line"] = line_no
        rec["_cols"] = len(parts)
        rows.append(rec)
    return rows, quarantine


def fingerprint(path):
    """변경 감지용 (mtime_ns, size). 파일이 없으면 None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read_file(path):
    """파일을 읽어 파싱 결과와 메타 정보를 돌려준다. 실패하면 OSError 를 그대로 올린다."""
    with open(path, "rb") as f:
        raw = f.read()
    text, enc = decode_bytes(raw)
    rows, quarantine = parse_text(text)
    st = os.stat(path)
    return {
        "rows": rows,
        "quarantine": quarantine,
        "encoding": enc,
        "size": len(raw),
        "mtime": st.st_mtime,
        "sha256": hashlib.sha256(raw).hexdigest()[:16],
    }

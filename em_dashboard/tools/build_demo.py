"""샘플 데이터를 넣은 단일 HTML 미리보기 생성 (서버 없이 열람용).
python tools/build_demo.py -> dist/demo.html
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import server, store, txt_reader  # noqa: E402

AS_OF = "2026-11-27"
# 미리보기용 가정값 (실제 대수는 config.ini [equipment_ovens] 에 적음)
DEMO_OVENS = {"MO-01": 24, "MO-02": 24, "MO-03": 16, "MO-04": 12, "MO-05": 5}


def demo_equipment(rows):
    """샘플에 '설비' 입력값을 지어 넣는다 (작업자가 입력한 것처럼). 진행중은 빈 Oven 이 많은 설비부터 채운다."""
    left = dict(DEMO_OVENS)
    edits = {}
    run = [r for r in rows if r.get("Test_in") and not r.get("Test_out")]
    run.sort(key=lambda r: -int((r.get("DUT") or "0").split("/")[0] or 0))
    for r in run:
        b = int((r.get("DUT") or "0").split("/")[0] or 0)
        name = max(left, key=lambda n: left[n])
        if left[name] < b:
            continue  # 자리가 없으면 미입력으로 남김 (화면의 "설비 미입력" 예시)
        start = DEMO_OVENS[name] - left[name] + 1
        left[name] -= b
        e = {"설비": {"value": name, "version": 1, "by": "샘플", "at": AS_OF + " 08:00:00"}}
        if name != "MO-04" or start > 1:  # MO-04 첫 건은 Oven 번호를 비워 둠 (화면의 "번호 미입력" 예시)
            ov = "%d-%d" % (start, start + b - 1) if b > 1 else str(start)
            e["Oven"] = {"value": ov, "version": 1, "by": "샘플", "at": AS_OF + " 08:00:00"}
        edits[store.row_key(r)] = e
    wait = [r for r in rows if r.get("PKG_in") and not r.get("Test_in")]
    for i, r in enumerate(wait[:4]):
        edits[store.row_key(r)] = {"설비": {"value": sorted(DEMO_OVENS)[i % len(DEMO_OVENS)], "version": 1, "by": "샘플", "at": AS_OF + " 08:00:00"}}
    return edits


def main():
    st = os.path.join(ROOT, "static")
    read = lambda n: open(os.path.join(st, n), encoding="utf-8").read()
    html = read("index.html")
    body = html.split("<!--BODY-->")[1].split("<!--/BODY-->")[0]
    import configparser, tempfile
    tmp = tempfile.mkdtemp()
    cfg_path = os.path.join(tmp, "config.ini")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(server.DEFAULT_CONFIG)
    cfg = server.load_config(cfg_path)
    d = txt_reader.read_file(os.path.join(ROOT, "data", "sample_em.txt"))
    data = {
        "rows": store.merge(d["rows"], demo_equipment(d["rows"]))[0], "deleted": [], "quarantine": d["quarantine"],
        "meta": {"revision": 1, "error": None, "loaded_at": AS_OF + " 08:00:00", "source": "sample_em.txt (가상 샘플)",
                 "encoding": d["encoding"], "version": server.APP_VERSION, "demo": True, "as_of": AS_OF},
        "config": {"poll_seconds": cfg["poll_seconds"], "editable": store.EDITABLE,
                   "key_fields": cfg["key_fields"], "ovens": DEMO_OVENS},
    }
    js_data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out = "\n".join([
        "<title>EM 평가 현황</title>",
        '<meta name="claude:viewport-fit-sides" content="cover">',
        "<style>", read("style.css"), "</style>",
        body,
        "<script>window.EMBEDDED_DATA = " + js_data + ";</script>",
        "<script>", read("rules.js"), "</script>",
        "<script>", read("app.js"), "</script>",
    ])
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    path = os.path.join(ROOT, "dist", "demo.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(path, len(out))


if __name__ == "__main__":
    main()

"""샘플 EM .txt 생성 (가상 데이터, 기준일 2026-11-27).

실제 데이터가 아니다. 화면 확인과 테스트용이며 일부러 데이터 확인 필요 행과 깨진 줄을 섞었다.
python tools/make_sample.py  -> data/sample_em.txt
"""
import datetime as dt
import os
import random

AS_OF = dt.date(2026, 11, 27)
rnd = random.Random(20261127)

PROCESSES = ["LN020LEP", "LN014LPP", "LN028HPC", "LN010LPE"]
TESTERS = ["CDS", "HJK", "YSB", "KMS", "JYJ"]  # 작업자 이니셜
ENGINEERS = ["JYJ", "KMS", "LHW", "PSY"]
VEHICLES = ["zmeit", "qtv21", "rk7m", "emv3"]
STRUCTS = ["M1_LINE", "M2_LINE", "V1_CHAIN", "V2_CHAIN", "M1_WIDE", "VIA_KELVIN"]


def d(days_ago):
    return (AS_OF - dt.timedelta(days=days_ago)).isoformat()


def lots():
    return "|".join("N%s%03d" % (rnd.choice("KWZQ") + rnd.choice("ABCDE"), rnd.randint(1, 999)) for _ in range(rnd.randint(1, 3)))


def row(i, stage, age, remark=None, tester=None, dut=None):
    boards = dut or rnd.choice([4, 6, 8, 8, 12])
    w = pl = pi = ti = to = "."
    t = age
    if stage == "WAFER_WAIT":
        pass
    elif stage == "PKG_LINE_W":
        w = d(t)
    elif stage == "PKG_LINE":
        pl = d(t); w = d(t + rnd.randint(2, 6))
    elif stage == "TEST_WAIT":
        pi = d(t); pl = d(t + rnd.randint(10, 18)); w = d(t + rnd.randint(20, 26))
    elif stage == "RUNNING":
        ti = d(t); pi = d(t + rnd.randint(1, 6)); pl = d(t + rnd.randint(12, 20)); w = d(t + rnd.randint(22, 30))
    elif stage == "DONE":
        to = d(t); ti = d(t + rnd.randint(28, 40)); pi = d(t + 45); pl = d(t + 60); w = d(t + 66)
    cols = [
        "EM", str(2800 + i), str(280 + i), rnd.choice(PROCESSES), ti, to, "%d/%d" % (boards, boards),
        str(boards * 16), w, pl, pi, rnd.choice(VEHICLES), tester or rnd.choice(TESTERS), rnd.choice(ENGINEERS),
    ]
    tail = [lots(), "|".join(rnd.sample(STRUCTS, rnd.randint(1, 3)))]
    if remark is None and rnd.random() < 0.5:
        return cols + tail  # 16컬럼 구형 줄
    return cols + [remark or "."] + tail


def build():
    out = []
    plan = (
        [("WAFER_WAIT", 0)] * 4
        + [("PKG_LINE_W", a) for a in (3, 9)]
        + [("PKG_LINE", a) for a in (2, 6, 11, 15, 19, 27, 33)]
        + [("TEST_WAIT", a) for a in (1, 3, 5, 9, 12)]
        + [("RUNNING", a) for a in (2, 6, 10, 14, 18, 21, 25, 30, 36, 47, 52)]
        + [("DONE", a) for a in (1, 4, 6, 12, 20, 33, 41, 58)]
    )
    for i, (stage, age) in enumerate(plan):
        remark = "오븐 온도 재확인 필요" if (stage == "RUNNING" and age == 47) else None
        out.append(",".join(row(i, stage, age, remark=remark)))
    # 데이터 확인이 필요한 줄 (의도적)
    bad = row(90, "DONE", 3)
    bad[4] = "."  # Test_in 없이 Test_out
    out.append(",".join(bad))
    rev = row(91, "RUNNING", 8)
    rev[10] = d(2)  # PKG_in 이 Test_in 보다 늦음
    out.append(",".join(rev))
    qty = row(92, "TEST_WAIT", 4)
    qty[7] = "120"  # 수량 불일치
    out.append(",".join(qty))
    dup = row(93, "PKG_LINE", 5)
    dup[2] = "283"  # List_no 중복
    out.append(",".join(dup))
    fmt = row(94, "RUNNING", 16)
    fmt[4] = "2026-13-02"  # 날짜 형식 오류
    out.append(",".join(fmt))
    # 깨진 줄 (격리 대상)
    out.append("EM,2999,399,LN020LEP,2026-11-01,.,8/8,128")
    out.append("EM,3000,400,LN014LPP,.,.,4/4,64,2026-11-20,.,.,qtv21,YSB,KMS,비고에,쉼표가,들어감,NKA001,M1_LINE")
    return out


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "data", "sample_em.txt")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(build()) + "\n")
    print(path)

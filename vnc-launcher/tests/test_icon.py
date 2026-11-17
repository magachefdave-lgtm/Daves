"""아이콘 파일이 빠지거나 깨지면 exe 빌드 전에 알 수 있게 한다 (Pillow 없이 머리만 읽는다)."""
import base64
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from launcher.icon_data import PNG


def test_window_icon_pngs():
    sizes = []
    for data in PNG:
        raw = base64.b64decode(data)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR"
        w, h = struct.unpack(">II", raw[16:24])
        assert w == h
        sizes.append(w)
    assert sizes == [16, 24, 32, 48, 64, 128]


def test_exe_icon_has_small_and_large_sizes():
    raw = open(os.path.join(ROOT, "assets", "app.ico"), "rb").read()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert (reserved, kind) == (0, 1) and count >= 6
    sizes = set()
    for i in range(count):
        w = raw[6 + 16 * i]
        size, offset = struct.unpack("<II", raw[6 + 16 * i + 8: 6 + 16 * i + 16])
        assert offset + size <= len(raw)
        sizes.add(w or 256)   # 0 = 256px
    assert {16, 24, 32, 48, 256} <= sizes

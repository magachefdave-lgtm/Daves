"""프로그램 아이콘 만들기 (개발용 - Pillow 필요: pip install pillow).

    python tools/make_icon.py

만드는 것
  assets/app.ico          exe 아이콘 (16~256px 여러 크기를 한 파일에)
  assets/icon_256.png     큰 그림 (README 등)
  launcher/icon_data.py   창 제목 표시줄·작업 표시줄 아이콘 (PNG 를 코드에 넣어 둔다)

모양: 어두운 바탕 위에 설비 버튼 3x3 격자, 가운데 하나가 "접속 중" 파란색.
프로그램 화면을 그대로 줄인 것이다. 48px 이하는 격자를 픽셀에 맞춰 그려 흐려지지 않게 한다.
"""
from __future__ import annotations

import base64
import io
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
WINDOW_SIZES = (16, 24, 32, 48, 64, 128)
SS = 8   # 이만큼 크게 그린 뒤 줄여서 가장자리를 매끄럽게 한다

BG_TOP, BG_BOTTOM = (50, 61, 75), (21, 26, 33)   # 화면 위쪽 바와 같은 짙은 회색, 위가 조금 밝다
RIM = (255, 255, 255, 34)                         # 어두운 작업 표시줄에서도 테두리가 보이게
TILE = (232, 236, 240)
TILE_SHADE = (205, 211, 218)
ACCENT_TOP, ACCENT_BOTTOM = (64, 128, 245), (31, 95, 214)   # 접속 중 (화면의 파랑보다 조금 밝게)
DOT = (91, 138, 114)                               # 정상 점 (화면과 같은 색)


def grid_px(n: int) -> tuple[int, int, int, int]:
    """작은 크기용: 바탕 한 변, 버튼 한 변, 간격, 시작 위치를 정수 픽셀로.

    격자가 바탕 가운데 오도록 고르고, 그렇게 안 되면 바탕을 1px 줄인다 (16px 은 15px 바탕).
    """
    best = None
    for bg in (n, n - 1):
        for t in range(3, n):
            for g in range(1, max(2, n // 6) + 1):
                total = 3 * t + 2 * g
                if total >= bg - 3 or (bg - total) % 2:
                    continue
                err = abs(total - 0.66 * n) + 2 * abs(g - 0.06 * n) + (0.6 if bg < n else 0)
                if best is None or err < best[0]:
                    best = (err, bg, t, g, (bg - total) // 2)
    return best[1:]


def vgradient(w: int, h: int, top, bottom) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        k = y / max(1, h - 1)
        c = tuple(round(a + (b - a) * k) for a, b in zip(top, bottom)) + (255,)
        for x in range(w):
            px[x, y] = c
    return img


def draw(n: int) -> Image.Image:
    S = n * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    small = n <= 48
    # 바탕: 둥근 사각형 + 위가 밝은 그라데이션
    if small:
        bg, t, g, off = grid_px(n)
        box = (0, 0, bg * SS - 1, bg * SS - 1)
        radius = round(0.21 * bg) * SS
    else:
        margin = round(0.03 * S)
        box = (margin, margin, S - margin - 1, S - margin - 1)
        radius = round(0.22 * S)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    img.paste(vgradient(S, S, BG_TOP, BG_BOTTOM), (0, 0), mask)
    d = ImageDraw.Draw(img)
    rim_w = max(SS, round(0.012 * S))
    d.rounded_rectangle(box, radius, outline=RIM, width=rim_w)

    # 버튼 격자
    if small:
        t, g, off = t * SS, g * SS, off * SS
    else:
        total = 0.62 * S
        g = 0.05 * S
        t = (total - 2 * g) / 3
        off = (S - total) / 2
    # 버튼 모서리: 아주 작은 버튼은 각지게 (둥글리면 십자 모양으로 뭉개진다)
    r = (0 if t <= 4 * SS else SS) if small else round(0.2 * t)
    for row in range(3):
        for col in range(3):
            x0, y0 = off + col * (t + g), off + row * (t + g)
            x1, y1 = x0 + t - 1, y0 + t - 1
            active = row == 1 and col == 1
            if not small:   # 버튼 아래 옅은 그림자
                d.rounded_rectangle((x0, y0 + 0.012 * S, x1, y1 + 0.012 * S), r, fill=(0, 0, 0, 70))
            if active:
                m = Image.new("L", (S, S), 0)
                ImageDraw.Draw(m).rounded_rectangle((x0, y0, x1, y1), r, fill=255)
                img.paste(vgradient(S, S, ACCENT_TOP, ACCENT_BOTTOM), (0, 0), m)
            else:
                d.rounded_rectangle((x0, y0, x1, y1), r, fill=TILE)
                if not small:   # 아래쪽을 살짝 어둡게 - 버튼이 떠 보이게
                    d.rounded_rectangle((x0, y0 + t * 0.55, x1, y1), r, fill=TILE_SHADE)
                    d.rectangle((x0, y0 + t * 0.55, x1, y0 + t * 0.55 + r), fill=TILE_SHADE)
                    d.rounded_rectangle((x0, y0, x1, y1 - t * 0.12), r, fill=TILE)
            if n >= 96:   # 큰 크기에서만: 화면처럼 정상 점 / 접속 중 막대
                cy = y0 + t / 2
                if active:
                    bw, bh = 0.07 * t, 0.36 * t
                    d.rectangle((x0 + 0.2 * t, cy - bh / 2, x0 + 0.2 * t + bw, cy + bh / 2), fill=(255, 255, 255))
                else:
                    dr = 0.08 * t
                    d.ellipse((x0 + 0.2 * t - dr, cy - dr, x0 + 0.2 * t + dr, cy + dr), fill=DOT)
    return img.resize((n, n), Image.LANCZOS)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main() -> None:
    images = {n: draw(n) for n in SIZES}
    os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
    ico = os.path.join(HERE, "assets", "app.ico")
    big = images[256]
    big.save(ico, format="ICO", sizes=[(n, n) for n in SIZES],
             append_images=[images[n] for n in SIZES if n != 256])
    lines = ['"""창 아이콘 (tools/make_icon.py 가 만든다 - 손으로 고치지 않는다)."""',
             "", "PNG = ("]
    for n in WINDOW_SIZES:
        data = base64.b64encode(png_bytes(images[n])).decode("ascii")
        lines.append(f'    "{data}",  # {n}px')
    lines.append(")")
    with open(os.path.join(HERE, "launcher", "icon_data.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    images[256].save(os.path.join(HERE, "assets", "icon_256.png"))   # README 등에 쓰는 큰 그림
    print("만들었습니다:", ico, os.path.getsize(ico), "bytes")


if __name__ == "__main__":
    main()

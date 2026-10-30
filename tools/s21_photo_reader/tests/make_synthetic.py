"""生成一张合成的 S21 "find dips" 图（风格与真实截图不同），用于回归测试。

用法:  python tests/make_synthetic.py out.png
真值同时写入 out_truth.json，供 tests/test_tool.py 比对。
"""
import json
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]

# 真值：比特名 → 波谷频率
DIPS = [
    ("q3_7",  5.812),
    ("q8_2",  5.967),
    ("q14_5", 6.115),
    ("q6_9",  6.238),
    ("q21_3", 6.402),
]
F0, F1 = 5.7, 6.5
AMP_TOP, AMP_BOT = 90000, 0


def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def build(path):
    W, H = 1200, 700
    L, R, T, B = 140, 1140, 90, 600
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f_tick, f_lab, f_small = font(19), font(22), font(16)

    def px(f):
        return L + (f - F0) / (F1 - F0) * (R - L)

    def py(a):
        return B - (a - AMP_BOT) / (AMP_TOP - AMP_BOT) * (B - T)

    # 坐标轴 + 网格
    for f in [5.7, 5.8, 5.9, 6.0, 6.1, 6.2, 6.3, 6.4, 6.5]:
        d.line([px(f), T, px(f), B], fill=(225, 225, 225))
        d.text((px(f), B + 12), f"{f:g}", fill="black", font=f_tick, anchor="ma")
    for a in range(0, 90001, 10000):
        d.line([L, py(a), R, py(a)], fill=(225, 225, 225))
        d.text((L - 14, py(a)), f"{a}", fill="black", font=f_tick, anchor="rm")
    d.rectangle([L, T, R, B], outline="black", width=2)
    d.text((L - 70, (T + B) / 2), "Amplitude", fill="black", font=f_lab, anchor="mm")
    d.text(((L + R) / 2, B + 55), "Frequency (GHz)", fill="black", font=f_lab, anchor="mm")
    d.text((L + 120, 30), "synthetic test - qubit read_adc1: S21 find dips", fill="black", font=f_small)

    # 曲线：基线 + 若干洛伦兹凹陷
    def amp(xf):
        a = 62000 + 18000 * math.cos((xf - 5.9) * 3.0)
        for _, f in DIPS:
            a -= 32000 * math.exp(-((xf - f) / 0.004) ** 2)
        return a

    pts = []
    x = F0
    while x <= F1:
        pts.append((px(x), py(amp(x))))
        x += (F1 - F0) / 900.0
    d.line(pts, fill=(20, 40, 160), width=2)

    # 标注：比特名在波谷左下方，数值在波谷正下方（与真实 find-dips 图排布一致）
    for name, f in DIPS:
        x_anchor, y_anchor = px(f), py(amp(f))
        d.text((x_anchor - 72, y_anchor + 10), name, fill="black", font=f_small)
        d.text((x_anchor, y_anchor + 10), f"{f:.3f}", fill="black", font=f_small)

    img.save(path)
    truth = {name: f for name, f in DIPS}
    Path(path).with_suffix(".truth.json").write_text(
        json.dumps(truth, indent=1), encoding="utf-8")
    return truth


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "synthetic_s21.png"
    t = build(out)
    print("synthetic figure:", os.path.abspath(out))
    print("truth:", t)

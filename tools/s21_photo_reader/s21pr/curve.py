"""曲线像素提取 + 波谷幅值 / 基线测量。

思路：屏蔽所有文字框与网格线后，剩下的暗像素即为数据曲线；
在每个波谷位置取曲线的最低点（波谷），在两侧取中位数为局部基线。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .ocr import TextBox


@dataclass
class Curve:
    mask: np.ndarray          # bool 2D：属于曲线的暗像素
    grid_x: list
    grid_y: list


def build_curve(gray: np.ndarray, boxes: list[TextBox], axes,
                grid_x: list, grid_y: list, dark_thr: int = 190,
                pad: int = 4) -> Curve:
    H, W = gray.shape
    dark = gray < dark_thr

    text_mask = np.zeros_like(dark)
    for bx in boxes:
        x0 = max(0, int(bx.x0) - pad)
        x1 = min(W, int(bx.x1) + pad)
        y0 = max(0, int(bx.y0) - pad)
        y1 = min(H, int(bx.y1) + pad)
        text_mask[y0:y1, x0:x1] = True

    mask = dark & (~text_mask)
    for gx in grid_x:
        mask[:, gx] = False
    for gy in grid_y:
        mask[gy, :] = False
    # 图框外不算曲线
    mask[:int(axes.top), :] = False
    mask[int(axes.bottom) + 1:, :] = False
    mask[:, :int(axes.left)] = False
    mask[:, int(axes.right) + 1:] = False
    return Curve(mask=mask, grid_x=list(grid_x), grid_y=list(grid_y))


def col_runs(mask: np.ndarray, x: int):
    col = mask[:, x]
    out, y = [], 0
    H = mask.shape[0]
    while y < H:
        if col[y]:
            s = y
            while y < H and col[y]:
                y += 1
            out.append((s, y - 1))
        y += 1
    return out


def trough_point(curve: Curve, x_anchor: int, half: int = 4,
                 text_boxes: list | None = None) -> tuple:
    """返回 (trough_py, obscured)。

    obscured=True 表示曲线被文字框挡住，trough_py 取遮挡框边缘（近似值）。
    """
    best = None
    for x in range(x_anchor - half, x_anchor + half + 1):
        if not (0 <= x < curve.mask.shape[1]):
            continue
        for (s, e) in col_runs(curve.mask, x):
            if best is None or e > best:
                best = e
    if best is None:
        return None, False

    if text_boxes:
        for bx in text_boxes:
            covers_x = bx.x0 - 4 <= x_anchor <= bx.x1 + 4
            if not covers_x:
                continue
            # 曲线在文字框上边缘处被切断 → 真正的波谷藏在文字后面
            if abs(best - bx.y0) <= 6:
                return float(bx.y0), True
    return float(best), False


def baseline_level(curve: Curve, x_anchor: int, other_x: list,
                   inner: int = 12, outer: int = 32) -> float | None:
    """局部基线：两侧窗口内曲线像素的中位 y（排除其它波谷附近）。"""
    ys = []
    H, W = curve.mask.shape
    for lo, hi in [(x_anchor - outer, x_anchor - inner), (x_anchor + inner, x_anchor + outer)]:
        for x in range(max(0, lo), min(W, hi + 1)):
            if any(abs(x - ox) < 10 for ox in other_x):
                continue
            for (s, e) in col_runs(curve.mask, x):
                ys.extend(range(s, e + 1))
    if not ys:
        return None
    return float(np.median(ys))

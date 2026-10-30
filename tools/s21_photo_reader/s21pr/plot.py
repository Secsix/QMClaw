"""图框检测、坐标轴标定、文本分类。

不依赖具体图：所有几何量都从像素自动推断。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .ocr import TextBox, is_number, is_numericish, parse_number, is_qubit


# ---------------------------------------------------------------- 图框 / 坐标轴
@dataclass
class Axes:
    left: float
    top: float
    right: float
    bottom: float
    x_of: object = None      # callable: value -> px
    y_of: object = None      # callable: px -> value
    x_of_inv: object = None  # callable: px -> value
    x_ticks: list = field(default_factory=list)   # [(value, px)]
    y_ticks: list = field(default_factory=list)   # [(value, py)]
    x_label: str = ""
    y_label: str = ""
    warnings: list = field(default_factory=list)


def _dark(gray: np.ndarray, thr: int) -> np.ndarray:
    return gray < thr


def find_axes(gray: np.ndarray, dark_thr: int = 200, coverage: float = 0.6) -> tuple:
    """返回 (left, top, right, bottom)：靠"长直线"找图框。"""
    H, W = gray.shape
    dark = _dark(gray, dark_thr)

    col_cnt = dark.sum(axis=0)
    cand_x = [x for x in range(W) if col_cnt[x] > coverage * H]
    if not cand_x:
        cand_x = [int(np.argmax(col_cnt))]
    left, right = min(cand_x), max(cand_x)

    row_cnt = dark[:, left:right + 1].sum(axis=1)
    cand_y = [y for y in range(H) if row_cnt[y] > coverage * (right - left + 1)]
    if not cand_y:
        cand_y = [int(np.argmax(row_cnt))]
    top, bottom = min(cand_y), max(cand_y)
    return float(left), float(top), float(right), float(bottom)


def _longest_span(flags) -> tuple:
    """返回 (起点, 终点)：允许最多 2px 间断的最长连续暗区段。"""
    best = (0, -1)
    start = None
    gap = 0
    for i, f in enumerate(flags):
        if f:
            if start is None:
                start = i
            gap = 0
            last = i
        else:
            if start is not None:
                gap += 1
                if gap > 2:
                    if last - start > best[1] - best[0]:
                        best = (start, last)
                    start = None
    if start is not None and last - start > best[1] - best[0]:
        best = (start, last)
    return best


def find_gridlines(gray: np.ndarray, axes: Axes, dark_thr: int = 200,
                   coverage: float = 0.85) -> tuple:
    """返回 (竖直网格线 x 列表, 水平网格线 y 列表)。

    判据：网格线几乎贯穿整个绘图区（上端贴顶、下端贴底）。
    曲线里陡峭的竖直段（例如波谷的悬崖）只占局部高度，不会被误判为网格线。
    """
    dark = _dark(gray, dark_thr)
    H, W = gray.shape
    l, t, r, b = int(axes.left), int(axes.top), int(axes.right), int(axes.bottom)
    vx, hy = [], []
    span_x = max(1, r - l)
    span_y = max(1, b - t)
    tol_y = 0.06 * span_y
    tol_x = 0.06 * span_x
    for x in range(l, r + 1):
        col = dark[t:b + 1, max(l, x - 1):min(r + 1, x + 2)].any(axis=1)
        s, e = _longest_span(col)
        if e - s >= coverage * span_y and s <= tol_y and e >= span_y - tol_y:
            vx.append(x)
    for y in range(t, b + 1):
        row = dark[max(t, y - 1):min(b + 1, y + 2), l:r + 1].any(axis=0)
        s, e = _longest_span(row)
        if e - s >= coverage * span_x and s <= tol_x and e >= span_x - tol_x:
            hy.append(y)
    return vx, hy


# ---------------------------------------------------------------- 线性标定
def _fit_linear(pairs):
    """pairs: [(value, pixel)] -> (slope, intercept) 最小二乘，跳掉离群点。"""
    pts = sorted(pairs, key=lambda p: p[1])
    if len(pts) < 2:
        return None
    v = np.array([p[0] for p in pts], dtype=float)
    p = np.array([p[1] for p in pts], dtype=float)
    keep = np.ones(len(v), dtype=bool)
    for _ in range(3):                       # 迭代剔除离群刻度（OCR 可能读错）
        if keep.sum() < 3:
            break
        A = np.vstack([v[keep], np.ones(keep.sum())]).T
        coef, *_ = np.linalg.lstsq(A, p[keep], rcond=None)
        resid = np.abs((v * coef[0] + coef[1]) - p)
        tol = max(3.0, 3.0 * np.median(resid[keep]))
        new = resid <= tol
        if new.sum() < 3 or (new == keep).all():
            keep = new if new.sum() >= 3 else keep
            break
        keep = new
    A = np.vstack([v[keep], np.ones(keep.sum())]).T
    coef, *_ = np.linalg.lstsq(A, p[keep], rcond=None)
    return float(coef[0]), float(coef[1])


# ---------------------------------------------------------------- 文本分类
@dataclass
class Classified:
    axes: Axes
    qubit_labels: list = field(default_factory=list)   # [TextBox]
    annotations: list = field(default_factory=list)    # [TextBox] 图内数值标注
    x_ticks: list = field(default_factory=list)
    y_ticks: list = field(default_factory=list)
    list_texts: list = field(default_factory=list)     # [TextBox] 形如 [a, b, c]
    titles: list = field(default_factory=list)
    figure_title: str = ""
    y_margin_text: str = ""      # 左侧竖排文字拼起来的碎片（旋转轴标题常被切碎）
    other: list = field(default_factory=list)


def classify(boxes: list[TextBox], axes: Axes) -> Classified:
    l, t, r, b = axes.left, axes.top, axes.right, axes.bottom
    res = Classified(axes=axes)

    for bx in boxes:
        txt = bx.text.strip()
        if not txt:
            continue
        inside = (bx.x0 >= l - 2 and bx.x1 <= r + 2 and bx.y0 >= t - 2 and bx.y1 <= b + 2)
        low = txt.lower()
        # 坐标轴标题
        if "freq" in low or "ghz" in low:
            axes.x_label = txt
            res.titles.append(bx)
            continue
        if (("amp" in low or "mag" in low or "s21" in low) and len(txt) <= 25
                and bx.x1 <= l + 12):
            axes.y_label = txt
            res.titles.append(bx)
            continue
        if is_qubit(txt):
            res.qubit_labels.append(bx)
            continue
        if txt.startswith("[") or txt.startswith("(") or "]" in txt:
            res.list_texts.append(bx)
            continue
        if is_numericish(txt):
            val = parse_number(txt)
            if bx.y0 >= b - 6:            # 底部刻度
                res.x_ticks.append((val, bx.cx))
            elif bx.x1 <= l + 6:          # 左侧刻度
                res.y_ticks.append((val, bx.cy))
            elif inside:                  # 图内数值标注（波谷处的数字）
                res.annotations.append(bx)
            else:
                res.other.append(bx)
            continue
        res.other.append(bx)

    res.x_ticks.sort(key=lambda p: p[1])
    res.y_ticks.sort(key=lambda p: p[1])
    res.annotations.sort(key=lambda p: p.x0)
    res.qubit_labels.sort(key=lambda p: p.cx)

    # 图题：坐标框上方、且不是坐标轴标题的那块最大文字
    above = [b for b in boxes if b.y1 <= t + 2 and b.h >= 8 and b not in res.titles]
    if above:
        res.figure_title = max(above, key=lambda b: b.w * b.h).text.strip()

    # 左侧竖排碎片（旋转的 Y 轴标题常被 OCR 切碎）
    frag = [fb for fb in boxes
            if fb.x1 <= l + 12 and fb.y0 >= t and fb.y1 <= b + 2
            and not is_numericish(fb.text) and fb.text.strip()]
    frag.sort(key=lambda fb: fb.y0)
    if frag:
        res.y_margin_text = "".join(fb.text.strip() for fb in frag)
        for kw in ("amplitude", "amp", "mag", "count"):
            if kw in res.y_margin_text.lower():
                axes.y_label = axes.y_label or kw
                break
    return res


def calibrate(gray: np.ndarray, boxes: list[TextBox], dark_thr: int = 200,
              coverage: float = 0.6) -> Classified:
    """一站式：找图框 → 分类文本 → 拟合坐标轴。"""
    l, t, r, b = find_axes(gray, dark_thr, coverage)
    axes = Axes(left=l, top=t, right=r, bottom=b)
    res = classify(boxes, axes)

    fx = _fit_linear(res.x_ticks)
    if fx:
        slope, inter = fx
        axes.x_of = lambda v, s=slope, i=inter: s * v + i
        axes.x_of_inv = lambda p, s=slope, i=inter: (p - i) / s
        axes.x_ticks = res.x_ticks
    else:
        axes.warnings.append("X 轴刻度不足，无法标定频率坐标")

    fy = _fit_linear(res.y_ticks)
    if fy:
        slope, inter = fy          # 拟合关系： pixel = slope * value + inter
        axes.y_of = lambda p, s=slope, i=inter: (p - i) / s
        axes.y_ticks = res.y_ticks
    else:
        axes.warnings.append("Y 轴刻度不足，无法标定幅值坐标")

    return res

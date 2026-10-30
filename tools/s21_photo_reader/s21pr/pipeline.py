"""端到端流水线：图片 → (比特号, 波谷值) 结果表。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import curve as curvemod
from . import ocr as ocrmod
from . import pairing, plot, recovery
from .ocr import parse_number


@dataclass
class Dip:
    qubit: str = ""
    value: float | None = None       # 图上标注的数值（波谷频率）
    ocr_value: float | None = None   # OCR 原始读数（可能与最终值不同）
    annot_text: str = ""
    anchor_x: float = 0.0            # 数值标注左边缘 px（matplotlib annotate 默认锚点）
    annot_y0: float = 0.0
    annot_y1: float = 0.0
    px_trough: float | None = None   # 波谷所在行 px
    trough_value: float | None = None  # 换算成纵轴数值
    baseline_value: float | None = None
    depth: float | None = None
    depth_pct: float | None = None
    obscured: bool = False
    pair_dx: float | None = None
    label_cx: float | None = None


@dataclass
class Result:
    image: str
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    x_range: tuple = ()
    y_range: tuple = ()
    dips: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    boxes: list = field(default_factory=list)
    axes: object = None

    @property
    def qubits(self):
        return [d.qubit for d in self.dips]


def _numbers_in(text: str):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]


def analyze(image_path, ocr_scale: float = 2.0, lib_dir: str | None = None,
            dark_thr: int = 190, grid_dark_thr: int = 200,
            dump_ocr: str | None = None) -> Result:
    boxes = ocrmod.run_ocr(image_path, scale=ocr_scale, lib_dir=lib_dir, dump=dump_ocr)
    gray = np.array(Image.open(image_path).convert("L"))

    cls = plot.calibrate(gray, boxes, dark_thr=grid_dark_thr)
    axes = cls.axes
    gx, gy = plot.find_gridlines(gray, axes, dark_thr=grid_dark_thr)

    res = Result(image=image_path, boxes=boxes, axes=axes)
    res.warnings.extend(axes.warnings)
    res.x_label = axes.x_label
    res.y_label = axes.y_label
    res.title = cls.figure_title

    if axes.x_ticks:
        res.x_range = (min(v for v, _ in axes.x_ticks), max(v for v, _ in axes.x_ticks))
    if axes.y_ticks:
        res.y_range = (min(v for v, _ in axes.y_ticks), max(v for v, _ in axes.y_ticks))

    curve = curvemod.build_curve(gray, boxes, axes, gx, gy, dark_thr=dark_thr)

    # ---- 每个数值标注 = 一个波谷
    listed = []
    for bx in cls.list_texts:
        listed += _numbers_in(bx.text)

    dips: list[Dip] = []
    for bx in cls.annotations:
        ocr_val = parse_number(bx.text)
        if ocr_val is None:
            continue
        d = Dip(value=ocr_val, annot_text=bx.text, anchor_x=bx.x0,
                annot_y0=bx.y0, annot_y1=bx.y1)
        x_anchor = int(round(bx.x0))
        py, obscured = curvemod.trough_point(curve, x_anchor, text_boxes=cls.annotations)
        d.px_trough = py
        d.obscured = obscured
        if py is not None and axes.y_of:
            d.trough_value = axes.y_of(py)

        # ---- 波谷数值的定值：几何位置优先，OCR 与图底列表做校验
        f_geo = axes.x_of_inv(x_anchor) if axes.x_of_inv else None
        cand = None
        if listed and f_geo is not None:
            best = min(listed, key=lambda v: abs(v - f_geo))
            if abs(best - f_geo) < 0.02:
                cand = best
        if cand is None:
            if f_geo is not None and abs(ocr_val - f_geo) <= max(0.02, 0.01 * abs(f_geo)):
                cand = ocr_val                      # OCR 与几何一致
            elif f_geo is not None:
                cand = round(f_geo, 4)             # OCR 首位数字被误认（如 6→5），以几何为准
                res.warnings.append(
                    f"标注 '{bx.text}' 与几何位置 {f_geo:.4f} 不符，已按几何位置取值 {cand}")
            else:
                cand = ocr_val
        if cand != ocr_val:
            d.ocr_value = ocr_val
        d.value = cand
        dips.append(d)

    # ---- 图底列表里有、但图内没识别出标注的波谷 → 用列表补齐
    if listed and axes.x_of:
        for v in listed:
            if any(abs(d.value - v) < 0.002 for d in dips):
                continue
            x_anchor = int(round(axes.x_of(v)))
            d = Dip(value=v, annot_text="(from list)", anchor_x=float(x_anchor),
                    annot_y0=0.0, annot_y1=0.0)
            py, obscured = curvemod.trough_point(curve, x_anchor, text_boxes=cls.annotations)
            d.px_trough = py
            d.obscured = obscured
            if py is not None and axes.y_of:
                d.trough_value = axes.y_of(py)
            res.warnings.append(f"图内未识别出 {v} 的数值标注，已按图底列表补齐（x={x_anchor}px）")
            dips.append(d)
        dips.sort(key=lambda d: d.anchor_x)

    # ---- 与底部列表交叉校验
    if listed and dips:
        got = sorted(round(d.value, 4) for d in dips)
        exp = sorted(round(v, 4) for v in listed)
        if len(got) != len(exp):
            res.warnings.append(f"波谷数({len(got)}) 与底部列表数({len(exp)}) 不一致")
        else:
            for a, b in zip(got, exp):
                if abs(a - b) > 0.0015:
                    res.warnings.append(f"图内标注 {a} 与底部列表 {b} 不一致")

    # ---- 基线
    anchor_list = [int(round(d.anchor_x)) for d in dips]
    for d, ax in zip(dips, anchor_list):
        others = [x for x in anchor_list if abs(x - ax) > 1]
        by = curvemod.baseline_level(curve, ax, others)
        if by is not None and axes.y_of:
            d.baseline_value = axes.y_of(by)
        if d.baseline_value is not None and d.trough_value is not None:
            d.depth = d.baseline_value - d.trough_value
            if d.baseline_value:
                d.depth_pct = 100.0 * d.depth / d.baseline_value

    # ---- 比特标签 ↔ 波谷 配对
    # 整图 OCR 漏掉的比特名，先在波谷附近裁剪放大二次识别补回来
    if cls.qubit_labels and len(cls.qubit_labels) < len(dips):
        recovered = recovery.recover_qubit_labels(
            image_path, dips, curve, axes,
            known_texts={b.text.strip() for b in cls.qubit_labels},
            known_boxes=list(cls.qubit_labels))
        if recovered:
            for d, nb in recovered:
                cls.qubit_labels.append(nb)
                res.warnings.append(f"局部放大重识别补回比特标签 {nb.text}")
            cls.qubit_labels.sort(key=lambda b: b.cx)
    if cls.qubit_labels and dips:
        for d in dips:
            d.px_trough = d.px_trough if d.px_trough is not None else d.anchor_x
        pairs = pairing.align(cls.qubit_labels, dips)
        for lb, d, c in pairs:
            d.qubit = lb.text.strip()
            d.label_cx = lb.cx
            d.pair_dx = abs(lb.cx - d.anchor_x)
        res.dips = [d for _, d, _ in pairs]
        unmatched = len(dips) - len(pairs)
        if unmatched:
            res.warnings.append(f"{unmatched} 个波谷未找到对应比特标签")
    else:
        res.dips = dips
        res.warnings.append("未识别到比特标签，仅输出波谷数值")

    # ---- 配对质量
    for d in res.dips:
        if d.pair_dx is not None and d.pair_dx > 60:
            res.warnings.append(f"{d.qubit} 与其数值标注横向错开 {d.pair_dx:.0f}px，请人工确认配对")
    return res

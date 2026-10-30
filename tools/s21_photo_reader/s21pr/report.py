"""输出：CSV / Markdown / 标注核对图。"""
from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image, ImageDraw


def write_csv(result, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["qubit", "s21_dip_value", "unit", "dip_x_px", "trough_px_y",
            "trough_amplitude", "baseline_amplitude", "dip_depth", "dip_depth_pct",
            "obscured", "pair_dx_px"]
    unit = "GHz" if "ghz" in (result.x_label or "").lower() else ""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for d in result.dips:
            w.writerow([
                d.qubit,
                f"{d.value:.4f}" if d.value is not None else "",
                unit,
                f"{d.anchor_x:.1f}",
                f"{d.px_trough:.0f}" if d.px_trough is not None else "",
                f"{d.trough_value:.0f}" if d.trough_value is not None else "",
                f"{d.baseline_value:.0f}" if d.baseline_value is not None else "",
                f"{d.depth:.0f}" if d.depth is not None else "",
                f"{d.depth_pct:.1f}" if d.depth_pct is not None else "",
                "yes" if d.obscured else "",
                f"{d.pair_dx:.0f}" if d.pair_dx is not None else "",
            ])
    return path


def write_markdown(result, path, method_note: str = ""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    unit = "GHz" if "ghz" in (result.x_label or "").lower() else ""
    lines = []
    lines.append(f"# S21 波谷识别结果 — {Path(result.image).name}\n")
    if result.title:
        lines.append(f"图题：`{result.title}`\n")
    if result.x_label:
        lines.append(f"X 轴：{result.x_label}"
                     + (f"（范围 {result.x_range[0]} ~ {result.x_range[1]}）" if result.x_range else ""))
    if result.y_label:
        lines.append(f"Y 轴：{result.y_label}"
                     + (f"（范围 {result.y_range[0]:.0f} ~ {result.y_range[1]:.0f}）" if result.y_range else ""))
    lines.append("")

    lines.append("## 主结果：比特号 → S21 波谷值\n")
    lines.append(f"| 比特号 | S21 波谷值 ({unit}) |")
    lines.append("|---|---|")
    for d in result.dips:
        v = f"{d.value:.4f}" if d.value is not None else ""
        lines.append(f"| {d.qubit} | {v} |")
    lines.append("")

    if any(d.trough_value is not None for d in result.dips):
        lines.append("## 附加：波谷幅值（由曲线栅格测得，误差约 ±5~10%）\n")
        lines.append("| 比特号 | 波谷幅值 | 局部基线 | 波谷深度 | 深度占比 |")
        lines.append("|---|---|---|---|---|")
        for d in result.dips:
            t = f"{d.trough_value:.0f}" if d.trough_value is not None else "—"
            b = f"{d.baseline_value:.0f}" if d.baseline_value is not None else "—"
            dep = f"{d.depth:.0f}" if d.depth is not None else "—"
            pct = f"{d.depth_pct:.1f}%" if d.depth_pct is not None else "—"
            lines.append(f"| {d.qubit} | {t} | {b} | {dep} | {pct} |")
        lines.append("")

    if result.warnings:
        lines.append("## 自检提示\n")
        for w in result.warnings:
            lines.append(f"- {w}")
        lines.append("")

    if method_note:
        lines.append("## 方法\n")
        lines.append(method_note)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_annotated(result, path):
    """画框 + 标波谷点，方便肉眼复核识别结果。"""
    img = Image.open(result.image).convert("RGB")
    dr = ImageDraw.Draw(img)
    for bx in result.boxes:
        txt = bx.text.strip()
        if not txt:
            continue
        if txt.lower().startswith("q") and any(ch.isdigit() for ch in txt):
            dr.rectangle([bx.x0, bx.y0, bx.x1, bx.y1], outline=(0, 160, 0), width=2)
        elif any(ch.isdigit() for ch in txt) and len(txt) <= 12:
            dr.rectangle([bx.x0, bx.y0, bx.x1, bx.y1], outline=(0, 0, 220), width=1)
    for d in result.dips:
        if d.px_trough is None:
            continue
        x = d.anchor_x
        y = d.px_trough
        dr.line([x - 8, y, x + 8, y], fill=(255, 0, 0), width=2)
        dr.line([x, y - 8, x, y + 8], fill=(255, 0, 0), width=2)
        dr.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(255, 0, 0), width=2)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path

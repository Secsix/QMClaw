"""命令行入口：单张图片或整目录批量处理。

用法：
    python cli.py <图片或目录> [-o 输出目录] [--no-annotate] [--ocr-scale 2.0]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from s21pr import pipeline, report
else:
    from . import pipeline, report

IMG_SUFFIX = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
# 工具自己产出的文件（标注核对图等），批量处理时跳过
SKIP_STEM_SUFFIX = ("_annotated",)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

METHOD_NOTE = """1. OCR（RapidOCR / ONNX，放大 2 倍）识别全部文字与坐标。
2. 自动定位图框与网格线，按刻度文字最小二乘拟合 X/Y 坐标轴。
3. 图内数值标注 = 波谷标注；比特名（q数字_数字）= 比特标签。
4. 曲线提取：屏蔽文字框与网格线后取暗像素，按 x 序对波谷取最低点得波谷幅值，
   两侧窗口取中位数得局部基线。
5. 比特标签与波谷标注用单调 DP 对齐配对（横向距离 + 纵向对齐双重代价）。
"""


def collect_images(path: Path):
    if path.is_file():
        return [path]
    out = []
    for p in sorted(path.rglob("*")):
        if not (p.is_file() and p.suffix.lower() in IMG_SUFFIX):
            continue
        if any(p.stem.endswith(sfx) for sfx in SKIP_STEM_SUFFIX):
            continue
        out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="S21 截图 → 比特号/波谷值 提取")
    ap.add_argument("target", help="图片文件或包含图片的目录")
    ap.add_argument("-o", "--outdir", default=None, help="输出目录（默认写到图片所在目录）")
    ap.add_argument("--ocr-scale", type=float, default=2.0, help="OCR 放大倍率")
    ap.add_argument("--lib-dir", default=None, help="OCR 依赖目录（默认 <repo>/.pylibs）")
    ap.add_argument("--no-annotate", action="store_true", help="不生成标注核对图")
    ap.add_argument("--dump-ocr", default=None, help="把 OCR 原始结果存成 JSON")
    ap.add_argument("--summary", default=None, help="批量模式下的汇总 CSV 路径")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"[ERR] 路径不存在: {target}", file=sys.stderr)
        return 2

    images = collect_images(target)
    if not images:
        print(f"[ERR] 没找到图片: {target}", file=sys.stderr)
        return 2

    outdir = Path(args.outdir) if args.outdir else target
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    failures = []
    for img in images:
        base = img.stem
        out_base = outdir / base
        dump = None
        if args.dump_ocr:
            dump = outdir / f"{base}.ocr.json"
        try:
            res = pipeline.analyze(img, ocr_scale=args.ocr_scale,
                                   lib_dir=args.lib_dir, dump_ocr=dump)
        except Exception as e:                       # 单张失败不影响整批
            failures.append((img, f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {img.name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            continue

        report.write_csv(res, out_base.with_suffix(".csv"))
        report.write_markdown(res, out_base.with_suffix(".md"), METHOD_NOTE)
        if not args.no_annotate:
            report.write_annotated(res, out_base.with_name(base + "_annotated.png"))

        print(f"[OK] {img.name}: {len(res.dips)} 个比特")
        for d in res.dips:
            v = f"{d.value:.4f}" if d.value is not None else "—"
            t = f"{d.trough_value:.0f}" if d.trough_value is not None else "—"
            print(f"      {d.qubit:10s} 波谷值={v:>9s}   波谷幅值={t:>8s}"
                  + ("  [波谷被标注遮挡]" if d.obscured else ""))
        for w in res.warnings:
            print(f"      ! {w}")

        rows.append({
            "image": img.name,
            "qubit": "; ".join(d.qubit for d in res.dips),
            "dip_values": "; ".join(f"{d.value:.4f}" if d.value is not None else ""
                                    for d in res.dips),
            "trough_amplitudes": "; ".join(f"{d.trough_value:.0f}" if d.trough_value is not None else ""
                                           for d in res.dips),
            "warnings": " | ".join(res.warnings),
        })

    if args.summary and rows:
        sp = Path(args.summary)
        sp.parent.mkdir(parents=True, exist_ok=True)
        with open(sp, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n汇总写入: {sp}")

    if failures:
        print(f"\n{len(failures)} 张失败:")
        for img, err in failures:
            print(f"  {img.name}: {err}")
        return 1
    print(f"\n完成: {len(rows)}/{len(images)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

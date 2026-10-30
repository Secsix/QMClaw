"""回归测试：合成图 → 工具输出 是否还原真值。

用法: python tests/test_tool.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent / ".pylibs"))

from make_synthetic import build            # noqa: E402
from s21pr import pipeline                  # noqa: E402


def main():
    tmp = ROOT / "_tmp_test"
    tmp.mkdir(exist_ok=True)
    img = tmp / "synthetic_s21.png"
    truth = build(img)

    res = pipeline.analyze(img, ocr_scale=2.0)
    print(f"识别到 {len(res.dips)} 个波谷")
    got = {}
    for d in res.dips:
        print(f"  {d.qubit:8s} value={d.value}  trough={d.trough_value}  baseline={d.baseline_value}")
        got[d.qubit] = d.value

    ok = True
    if set(got) != set(truth):
        print(f"[FAIL] 比特集合不一致: 得到 {sorted(got)}，期望 {sorted(truth)}")
        ok = False
    for name, val in truth.items():
        if name not in got:
            ok = False
            continue
        if abs(got[name] - val) > 2e-3:
            print(f"[FAIL] {name}: 得到 {got[name]}，期望 {val}")
            ok = False
    for w in res.warnings:
        print(f"  ! {w}")
    print("\n测试结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

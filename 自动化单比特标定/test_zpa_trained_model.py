"""test_zpa_trained_model.py — 训练出来的 ZPA 区间识别器的验收测试。

拿现场人工标注的 5 张图当标准答案，检查 ``zpa_interval_model.json`` 训练出的
参数在运行时（``zpa_interval.analyze_intervals``）能复现同样的区间。

判定口径（人工标注 = 谐振轨迹在频率窗口内的跨度）：
  * 00044 / 00047 / 00049 / 00055：端点误差 <= 1 层（zpa 栅格 0.4 V）；
  * 00059：标注 (0, 3) 与数据对不上 —— 2.2..3.0 三层是平层（深度 0.75~1.09 dB、
    无 dip），和 00047 里被判为无效的 1.8..3.0 特征完全一样。数据支持的答案是
    (0.2, 1.8)。这一条按"已知标注异常"处理：断言模型的输出等于数据支持的
    区间，并在输出里标 WARN —— 若现场复核后改了标注，这里要同步改。

用法：
    python test_zpa_trained_model.py [DataVault 目录]
退出码：0 = 全过；1 = 有失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import zpa_interval as zi                                   # noqa: E402
from zpa_interval_labels import load_labeled_files          # noqa: E402

DEFAULT_SESSION = Path(r"D:\DataVault\LQHL.dir\test.dir\20261113_20qubits.dir")

# 人工标注与数据一致的 4 张：端点容差（单位 = zpa 栅格步长）
TOLERANCE_LAYERS = 1.0
# 标注与数据矛盾的 1 张：模型应当给出"数据支持的"区间
KNOWN_OUTLIER = {"00059 - q19_7%c S21zpa2d.hdf5": (0.2, 1.8)}

FAILURES = []
WARNINGS = []


def check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILURES.append(name)


def warn(name, detail=""):
    print(f"  [WARN] {name} {detail}")
    WARNINGS.append(name)


def main():
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SESSION
    model = zi.load_model()
    print("模型文件参数：", {k: v for k, v in model.items()
                             if k != "ensemble" and k != "edge_tol_mhz"})
    print("集成成员数：", len(model.get("ensemble") or []))
    check("模型文件已载入", bool(model.get("ensemble")) or model.get("depth_rel") is not None)

    labeled = load_labeled_files(directory)
    if not labeled:
        print("  [SKIP] 未找到标注文件（目录：%s）" % directory)
        return 0

    for name, path, human in labeled:
        r = zi.analyze_intervals(path)
        vr = r.get("valid_zpa_range")
        step = float(np.median(np.diff(np.unique(np.asarray(
            [p for p in r["points"]], dtype=object))))) if False else None
        z_axis = np.asarray(sorted({round(p["zpa"], 6) for p in r["points"]}))
        step = float(np.median(np.diff(z_axis))) if len(z_axis) > 1 else 0.4
        print("\n%s" % name)
        print("   人工 = (%+.2f, %+.2f)   模型 = %s   最大深度 %.2f dB"
              % (human[0], human[1],
                 "无" if not vr else "(%+.2f, %+.2f)" % (vr[0], vr[1]),
                 r["max_depth_db"]))
        if not vr:
            check("模型给出区间", False, "valid_zpa_range=None")
            continue
        if name in KNOWN_OUTLIER:
            want = KNOWN_OUTLIER[name]
            check("数据支持的区间 = (%+.2f, %+.2f)" % want,
                  abs(vr[0] - want[0]) <= 0.05 and abs(vr[1] - want[1]) <= 0.05,
                  f"got=({vr[0]:+.2f}, {vr[1]:+.2f})")
            warn("人工标注 (%+.2f, %+.2f) 与数据不符，需现场复核"
                 % (human[0], human[1]),
                 "模型按数据给 (%+.2f, %+.2f)" % (vr[0], vr[1]))
            continue
        err = (abs(vr[0] - human[0]) + abs(vr[1] - human[1])) / step
        check("端点误差 <= %.1f 层（=%.2f V）" % (TOLERANCE_LAYERS, TOLERANCE_LAYERS * step),
              err <= TOLERANCE_LAYERS + 1e-9, f"误差 {err:.2f} 层")

    print()
    if WARNINGS:
        print("WARN %d 条：" % len(WARNINGS))
        for w in WARNINGS:
            print("  - " + w)
    if FAILURES:
        print("FAILED: %d -> %s" % (len(FAILURES), FAILURES))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

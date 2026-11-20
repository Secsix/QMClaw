"""validate_zpa_intervals.py — 拿历史 S21ZPA 图对比两套区间判定规则（纯离线）。

- OLD：旧规则（逐层 argmin + 局部 find_peaks prominence），见 plan_zpa_bias.plan_legacy
- NEW：当前规则（dip 深度 + 分支跟踪），见 zpa_interval.analyze_intervals

不连服务、不碰硬件：只读 DataVault 里已存在的 HDF5 文件。
用法：
    python validate_zpa_intervals.py [目录]
默认目录 = 当前 session 的 DataVault 目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_DIR = Path(r"D:\DataVault\LQHL.dir\test.dir\20261113_20qubits.dir")


def legacy_intervals(path):
    from classify_zpa_datavault import load_model
    from plan_zpa_bias import plan_legacy
    try:
        r = plan_legacy(path, load_model(), 1.0, 3)
        return r["intervals"], r["file_normal_prediction"]
    except Exception as exc:
        return [], "ERR:%s" % type(exc).__name__


def main():
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    if not directory.is_dir():
        print("目录不存在：%s" % directory)
        return 2
    files = sorted(p for p in directory.glob("*S21zpa2d*.hdf5"))
    from zpa_interval import analyze_intervals
    print("%-34s %-4s %-4s | %-26s | %-26s | %s" %
          ("file", "nf", "nz", "OLD", "NEW", "normal"))
    ok_old = ok_new = 0
    locked = 0
    for p in files:
        o_int, normal = legacy_intervals(p)
        try:
            n = analyze_intervals(p)
        except (PermissionError, OSError) as exc:
            locked += 1
            print("%-34s LOCKED（LabRAD 正在写）" % p.name[:34])
            continue
        o_txt = ";".join("%.2f..%.2f(%d)" % (i["sampled_zpa_interval"][0],
                                             i["sampled_zpa_interval"][1],
                                             i["point_count"]) for i in o_int) or "-"
        n_txt = ";".join("%.2f..%.2f(%d)" % (i["sampled_zpa_interval"][0],
                                             i["sampled_zpa_interval"][1],
                                             i["point_count"]) for i in n["intervals"]) or "-"
        ok_old += bool(o_int)
        ok_new += bool(n["intervals"])
        print("%-34s %-4d %-4d | %-26s | %-26s | %s" %
              (p.name[:34], n["n_freq"], n["n_zpa"], o_txt, n_txt, normal))
    print()
    print("可读文件 %d（另有 %d 个正被 LabRAD 占用）：OLD 有区间 %d，NEW 有区间 %d"
          % (len(files) - locked, locked, ok_old, ok_new))
    return 0


if __name__ == "__main__":
    sys.exit(main())

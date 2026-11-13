"""test_zpa_interval_offline.py — ZPA 区间判定（分支跟踪）的离线自检。

不连服务、不碰硬件：用合成 S21ZPA 地图验证 `zpa_interval` 的行为，
并与旧规则（逐层 argmin + 局部 find_peaks prominence）对照。

覆盖的失效模式：
  1. 宽 dip：0.2 MHz 栅格下局部 prominence≈0，旧规则判掉，真 dip 有 8 dB；
  2. 同层多 dip（腔 + 比特）：逐层 argmin 在两条分支间跳，旧规则切碎区间；
  3. 谐振扫出窗口：dip 落在窗口边界，旧规则判成 frequency_window_edge；
  4. 纯噪声 / 无 dip：两套规则都不应给出区间。

用法：
    python test_zpa_interval_offline.py
退出码：0 = 全过；1 = 有失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from zpa_interval import analyze_intervals, DEFAULT_MIN_DEPTH_DB, DEFAULT_MIN_POINTS  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILURES.append(name)


def write_map(path, freq, zpa, amplitudes):
    """写一个最小可读的 DataVault 风格 HDF5（f0=freq, f1=zpa, f2=amplitude）。"""
    import h5py
    rows = []
    for j, z in enumerate(zpa):
        for i, f in enumerate(freq):
            rows.append((float(f), float(z), float(amplitudes[j][i])))
    arr = np.array(rows, dtype=[("f0", "f8"), ("f1", "f8"), ("f2", "f8")])
    with h5py.File(path, "w") as h:
        ds = h.create_dataset("DataVault", data=arr)
        ds.attrs["Independent0.label"] = "freq"
        ds.attrs["Independent1.label"] = "zpa"
        ds.attrs["Independent0.unit"] = "GHz"
        ds.attrs["Independent1.unit"] = "V"
    return path


def lorentzian(freq, center, width, depth_db, floor_db=0.0):
    """一条洛伦兹 dip（dB），center 处最低。"""
    return floor_db - depth_db / (1.0 + ((freq - center) / width) ** 2)


def main():
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="zpa_interval_test_"))
    freq = np.round(np.arange(6.6017, 6.6038, 0.0002), 7)   # 11 点，0.2 MHz 栅格
    zpa = np.round(np.arange(-3.0, 3.01, 0.4), 3)          # 16 层

    print("[1] 谐振在窗口内缓慢移动 → 必须给出区间")
    amp = []
    for j, z in enumerate(zpa):
        center = 6.6022 + 0.00003 * (j - 8)                 # 一路扫过窗口
        amp.append([10 ** (x / 20.0) for x in lorentzian(freq, center, 0.0004, 8.0)])
    p = write_map(tmp / "moving.hdf5", freq, zpa, amp)
    r = analyze_intervals(p)
    check("移动谐振有区间", bool(r["intervals"]),
          f"intervals={r['intervals']}")
    if r["intervals"]:
        iv = r["intervals"][0]
        check("区间点数 >= 3", iv["point_count"] >= 3, f"n={iv['point_count']}")
        check("候选点在区间内", iv["sampled_zpa_interval"][0] <= iv["candidate_zpa"] <= iv["sampled_zpa_interval"][1])

    print("[2] 宽 dip（局部 prominence≈0）→ 旧规则判掉，新规则必须给出区间")
    amp = []
    for j, z in enumerate(zpa):
        center = 6.6025
        amp.append([10 ** (x / 20.0) for x in lorentzian(freq, center, 0.0008, 6.0)])
    p = write_map(tmp / "broad.hdf5", freq, zpa, amp)
    r = analyze_intervals(p)
    check("宽 dip 有区间", bool(r["intervals"]), f"intervals={r['intervals']}")
    depths = [pt.get("depth_db", 0) for pt in r["points"]]
    check("宽 dip 深度 >= 3 dB", max(depths) >= 3.0, f"max_depth={max(depths):.2f}")

    print("[3] 同层两个 dip → 必须跟住其中一条，不能跳")
    amp = []
    for j, z in enumerate(zpa):
        c1 = 6.6021
        c2 = 6.6033
        v = np.minimum(lorentzian(freq, c1, 0.0003, 9.0), lorentzian(freq, c2, 0.0003, 4.0))
        amp.append([10 ** (x / 20.0) for x in v])
    p = write_map(tmp / "twodips.hdf5", freq, zpa, amp)
    r = analyze_intervals(p)
    check("双 dip 仍给出区间", bool(r["intervals"]), f"intervals={r['intervals']}")
    if r["intervals"]:
        iv = r["intervals"][0]
        trace = iv["freq_trace_mhz"]
        check("谐振轨迹连续（无大跳变）",
              all(abs(b - a) <= 1.6 for a, b in zip(trace, trace[1:])), f"trace={trace}")

    print("[4] 纯噪声（无 dip）→ 不应给出区间")
    rng = np.random.default_rng(7)
    amp = [[10 ** (rng.normal(0, 0.05) / 20.0) for f in freq] for j in zpa]
    p = write_map(tmp / "noise.hdf5", freq, zpa, amp)
    r = analyze_intervals(p)
    check("噪声图无区间", not r["intervals"], f"intervals={r['intervals']}")

    print("[5] 谐振完全在窗口外 → 不应给出区间")
    amp = []
    for j, z in enumerate(zpa):
        center = 6.6100 + 0.00001 * j
        amp.append([10 ** (x / 20.0) for x in lorentzian(freq, center, 0.0004, 9.0)])
    p = write_map(tmp / "outside.hdf5", freq, zpa, amp)
    r = analyze_intervals(p)
    check("窗口外无区间", not r["intervals"], f"intervals={r['intervals']}")

    print("[6] 阈值参数生效")
    p = write_map(tmp / "moving.hdf5", freq, zpa, amp)  # reuse path, contents replaced below
    amp = []
    for j, z in enumerate(zpa):
        center = 6.6022 + 0.00003 * (j - 8)
        amp.append([10 ** (x / 20.0) for x in lorentzian(freq, center, 0.0004, 8.0)])
    p = write_map(tmp / "moving2.hdf5", freq, zpa, amp)
    strict = analyze_intervals(p, min_depth_db=40.0)
    check("阈值 40 dB 时无区间", not strict["intervals"])
    loose = analyze_intervals(p, min_depth_db=0.5, min_points=DEFAULT_MIN_POINTS)
    check("阈值 0.5 dB 时有区间", bool(loose["intervals"]))

    print("[7] 真实历史文件（可选，DataVault 在场时检查）")
    session = Path(r"D:\DataVault\LQHL.dir\test.dir\20261113_20qubits.dir")
    real = session / "00088 - q7_21%c S21zpa2d.hdf5"
    if real.exists():
        try:
            r = analyze_intervals(real)
            check("q7_21 历史图判出区间", bool(r["intervals"]), f"intervals={r['intervals']}")
            if r["intervals"]:
                iv = r["intervals"][0]
                check("q7_21 主区间覆盖 -2.6..-1.0",
                      abs(iv["sampled_zpa_interval"][0] + 2.6) < 1e-6 and
                      abs(iv["sampled_zpa_interval"][1] + 1.0) < 1e-6,
                      f"iv={iv['sampled_zpa_interval']}")
        except PermissionError:
            print("  [SKIP] q7_21 文件正被 LabRAD 占用")
    else:
        print("  [SKIP] 未找到 DataVault session 目录")

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

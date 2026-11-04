"""DataVault 单比特体检报告。

用法：
    python health_report.py --vault "D:\\DataVault\\LQHL.dir\\test.dir\\20260822_device1.dir" \
        --start 3648 --end 4455 -o out

输出：
    <out>/qubit_health.csv     每比特一行的指标表
    <out>/qubit_health.md      可读报告（含判活结论）
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path

import numpy as np

from dvread import list_datasets, read_dataset
from metrics import METRIC_BY_EXP

# 文件读取缓存（同一文件会被多次用到）
_CACHE: dict = {}


def _read(ds):
    key = str(ds.path)
    if key not in _CACHE:
        _CACHE[key] = read_dataset(ds)
    return _CACHE[key]

# 判活阈值（用户给定的口径）
IQRAW_THRESH = 0.1           # IQraw 单发 SNR > 0.1
RABI_CONTRAST_THRESH = 0.15   # Rabi 振荡的 P1 峰谷落差下限（见 metrics.RABI_SWING_THRESH）


def fmt(v, nd=4):
    if v is None:
        return ""
    if isinstance(v, float):
        if not np.isfinite(v):
            return "nan"
        return f"{v:.{nd}g}"
    return str(v)


def analyze(vault: str, start=None, end=None):
    """按比特聚合所有实验数据。"""
    datasets = list_datasets(vault, start, end)
    by_qubit: dict[str, dict] = {}
    for ds in datasets:
        q = by_qubit.setdefault(ds.qubit, {"files": [], "exp": {}})
        q["files"].append(ds.index)
        bucket = q["exp"].setdefault(ds.exp, [])
        bucket.append(ds)

    results = []
    for qubit in sorted(by_qubit):
        info = by_qubit[qubit]
        row = {"qubit": qubit, "n_files": len(info["files"]),
               "index_first": min(info["files"]), "index_last": max(info["files"])}

        # ---- 寄存器参数（整体最后一次记录值 = 收尾状态） ----
        last_ds = max((d for lst in info["exp"].values() for d in lst), key=lambda d: d.index)
        regs = _read(last_ds).qubit_params()
        row.update({
            "zpa": regs.get("bias_z"),          # zpa = 比特直流偏置（regs.bias_z）
            "zpa_PiGate": regs.get("PiGate.zpa"),
            "f10": regs.get("f10"), "f21": regs.get("f21"),
            "fread": regs.get("fread"), "fc": regs.get("fc"),
            "fc_read": regs.get("fc_read"), "bias_z": regs.get("bias_z"),
            "PiGate_amp": regs.get("PiGate.amp"), "PiHalf_amp": regs.get("PiHalf.amp"),
            "PiGate_length": regs.get("PiGate.length"),
            "ReadIn_power": regs.get("ReadIn.power"),
            "ReadIn_ring_power": regs.get("ReadIn.ring_power"),
        })

        # ---- IQraw：整段扫描取最好的一条（按 SNR） ----
        iq_files = info["exp"].get("IQraw", [])
        best_iq, best_snr, best_norm = None, 0.0, 0.0
        iq_norm_best = 0.0
        for ds in iq_files:
            m = METRIC_BY_EXP["IQraw"](_read(ds).columns)
            if not m:
                continue
            if m["snr"] > best_snr:
                best_iq, best_snr, best_norm = ds.index, m["snr"], m["norm"]
            iq_norm_best = max(iq_norm_best, m["norm"])
        row["iqraw_snr"] = best_snr
        row["iqraw_norm"] = best_norm
        row["iqraw_norm_best"] = iq_norm_best
        row["iqraw_best_index"] = best_iq
        row["iqraw_n_files"] = len(iq_files)
        # 最好那条 IQraw 对应的寄存器（工作点）
        iq_ds = next((d for d in iq_files if d.index == best_iq), None)
        iq_regs = _read(iq_ds).qubit_params() if iq_ds else {}
        row["iq_regs"] = iq_regs
        row["iq_f10"], row["iq_zpa"], row["iq_fread"] = (
            iq_regs.get("f10"), iq_regs.get("bias_z"), iq_regs.get("fread"))

        # ---- PiPulse：Rabi 振荡，取通过判据且最显著的一条 ----
        rabi_files = info["exp"].get("PiPulse", [])
        best_rabi, best_score = None, 0.0
        rabi_any = False
        for ds in rabi_files:
            m = METRIC_BY_EXP["PiPulse"](_read(ds).columns)
            if not m or not m.get("ok"):
                continue
            rabi_any = True
            if m.get("score", 0) > best_score:
                best_rabi, best_score = (ds.index, m), m["score"]
        row["rabi_contrast"] = best_rabi[1].get("swing") if best_rabi else 0.0
        row["rabi_score"] = best_score
        row["rabi_amp_opt"] = best_rabi[1].get("amp_opt") if best_rabi else None
        row["rabi_period"] = best_rabi[1].get("period") if best_rabi else None
        row["rabi_full_osc"] = bool(best_rabi)
        row["rabi_index"] = best_rabi[0] if best_rabi else None
        row["rabi_n_files"] = len(rabi_files)
        rb_ds = next((d for d in rabi_files if d.index == row["rabi_index"]), None)
        rb_regs = _read(rb_ds).qubit_params() if rb_ds else {}
        row["rabi_regs"] = rb_regs
        row["rabi_f10"], row["rabi_zpa"], row["rabi_fread"] = (
            rb_regs.get("f10"), rb_regs.get("bias_z"), rb_regs.get("fread"))

        # PiPulse df / Ramsey df / Spectroscopy / S21_dis 已在上方按工作点选取

        # ---- 判活 ----
        iq_ok = row["iqraw_snr"] > IQRAW_THRESH
        rabi_ok = bool(rabi_any)
        row["iqraw_ok"] = iq_ok
        row["piamp_ok"] = rabi_ok
        row["alive"] = bool(iq_ok and rabi_ok)

        # ---- 工作点（f10, zpa）上的其它性质 ----
        # 工作点 = IQraw 最优那条；若 IQraw 不过但 Rabi 过，则取 Rabi 那条
        wp_regs = iq_regs if iq_ok else (rb_regs if rabi_ok else iq_regs)
        row["wp_regs"] = wp_regs
        row["wp_f10"] = wp_regs.get("f10")
        row["wp_zpa"] = wp_regs.get("bias_z")
        row["wp_fread"] = wp_regs.get("fread")
        row["wp_f21"] = wp_regs.get("f21")
        row["wp_fc"] = wp_regs.get("fc")
        row["wp_fc_read"] = wp_regs.get("fc_read")

        # fread 在整段扫描中的稳健统计（剔除明显异常值，如误写入的 182 GHz）
        fr = []
        for exp, lst in info["exp"].items():
            for ds in lst:
                v = _read(ds).qubit_params().get("fread")
                if v is not None and 0.1 < v < 100:
                    fr.append(float(v))
        if fr:
            row["fread_median"] = float(np.median(fr))
            row["fread_min"], row["fread_max"] = float(min(fr)), float(max(fr))
            if not (0.1 < (row["wp_fread"] or 0) < 100):
                row["wp_fread_raw"] = row["wp_fread"]
                row["wp_fread"] = row["fread_median"]
                row["fread_replaced"] = True

        def _dist(ds):
            """文件寄存器与工作点的距离（f10 主，zpa 次）。"""
            r = _read(ds).qubit_params()
            df = abs((r.get("f10") or 0) - (row["wp_f10"] or 0))
            dz = abs((r.get("bias_z") or 0) - (row["wp_zpa"] or 0))
            return df + 0.1 * dz

        for exp, keys in (("PiPulse df", ("df_peak",)),
                          ("Ramsey df", ("T2", "T2star", "detune_MHz", "osc_freq_MHz",
                                        "period_ns", "r2", "valid")),
                          ("S21_dis", ("fread_meas_amp0", "distance01_max"))):
            lst = info["exp"].get(exp, [])
            if not lst:
                continue
            # 取与工作点最近的一条（各实验在 (f10, zpa) 网格上各扫一遍）
            ds = min(lst, key=_dist) if wp_regs.get("f10") else lst[-1]
            m = METRIC_BY_EXP[exp](_read(ds).columns) or {}
            for k in keys:
                row[f"{exp.replace(' ', '_')}_{k}"] = m.get(k)
            row[f"{exp.replace(' ', '_')}_index"] = ds.index
            row[f"{exp.replace(' ', '_')}_f10"] = _read(ds).qubit_params().get("f10")
            row[f"{exp.replace(' ', '_')}_zpa"] = _read(ds).qubit_params().get("bias_z")
            # S21 谱扫频率落在明显异常区间（寄存器被误写）时改用工作点 fread
            key = f"{exp.replace(' ', '_')}_fread_meas_amp0"
            v = row.get(key)
            if v is not None and not (0.1 < v < 100) and row.get("wp_fread"):
                row[key + "_raw"] = v
                row[key] = row["wp_fread"]

        # Spectroscopy：谱扫得到的比特频率 = P1(freq) 共振峰
        # 优先取“峰显著且贴近工作点 f10”的一条；否则取最显著的一条并标记冲突
        spec_info = []
        for ds in info["exp"].get("Spectroscopy", []):
            m = METRIC_BY_EXP["Spectroscopy"](_read(ds).columns)
            if not m:
                continue
            sig = m.get("p1_peak", 0) - m.get("p1_base", 0)
            spec_info.append((ds, m, sig))
        wp_f10 = row["wp_f10"]
        cand = [s for s in spec_info
                if s[2] >= 0.15 and wp_f10 and abs(s[1]["f10_meas"] - wp_f10) <= 0.15]
        conflict = False
        if cand:
            pick = min(cand, key=lambda s: abs(s[1]["f10_meas"] - wp_f10))
        elif spec_info:
            pick = max(spec_info, key=lambda s: s[2])
            conflict = True
        else:
            pick = None
        if pick:
            row["Spectroscopy_f10_meas"] = pick[1]["f10_meas"]
            row["Spectroscopy_p1_peak"] = pick[1].get("p1_peak")
            row["Spectroscopy_p1_base"] = pick[1].get("p1_base")
            row["Spectroscopy_sig"] = pick[2]
            row["Spectroscopy_index"] = pick[0].index
            row["Spectroscopy_conflict"] = conflict

        # fread 统计已在上方计算
        results.append(row)
    return results


def write_csv(rows, path: Path):
    if not rows:
        return
    keys = [k for k in rows[0].keys() if not isinstance(rows[0][k], dict)]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (fmt(v) if isinstance(v, float) else v) for k, v in r.items()})


def write_md(rows, path: Path, vault: str, start, end):
    alive = [r for r in rows if r["alive"]]
    lines = []
    lines.append("# DataVault 单比特体检报告\n")
    lines.append(f"- 数据源：`{vault}`")
    lines.append(f"- 序号区间：{start} – {end}")
    lines.append(f"- 生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"- 比特总数：{len(rows)}；判活 **{len(alive)}** 个\n")
    lines.append("## 判活口径（用户定义）\n")
    lines.append(f"1. **IQraw > {IQRAW_THRESH}**：单发 SNR = |z₁-z₀| / √((σ₀²+σ₁²)/2)，"
                 "同时给出归一化分离度 |z₁-z₀|/(|z₀|+|z₁|) 作参考。")
    lines.append(f"2. **PiAmp 实验正常**：PiPulse 的 P1(piAmp) 出现 Rabi 振荡"
                 f"（内部极大值 + 峰后回落），峰谷落差 ≥ {RABI_CONTRAST_THRESH} 且 ≥ 4×噪声σ。\n")

    lines.append("## 一、判活结论\n")
    lines.append("| 比特 | 结论 | IQraw SNR | IQraw归一化 | Rabi峰谷落差 | 最优pi幅度 | "
                 "zpa | f10 (GHz) | f21 (GHz) | fread (GHz) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        reasons = []
        if not r["iqraw_ok"]:
            reasons.append("IQraw低")
        if not r["piamp_ok"]:
            reasons.append("Rabi无振荡")
        mark = "✅ 活" if r["alive"] else "❌ " + "/".join(reasons)
        lines.append(
            f"| {r['qubit']} | {mark} | {fmt(r['iqraw_snr'], 3)} | {fmt(r['iqraw_norm_best'], 3)} | "
            f"{fmt(r['rabi_contrast'], 3)} | {fmt(r['rabi_amp_opt'], 3)} | "
            f"{fmt(r['wp_zpa'], 4)} | {fmt(r['wp_f10'], 6)} | {fmt(r['wp_f21'], 6)} | "
            f"{fmt(r['wp_fread'], 6)} |")

    lines.append("\n> zpa 取 `regs.bias_z`（比特直流偏置）；f10/f21/fread 为该比特**工作点**"
                 "（判活那条记录）上的寄存器值。f21 = f10 − 0.22 GHz（各比特一致）。\n")

    lines.append("## 二、活比特参数明细\n")
    if alive:
        lines.append("| 比特 | zpa (bias_z) | f10 (GHz) | f21 (GHz) | fread (GHz) | fc (GHz) | "
                     "fc_read (GHz) | PiGate.amp | PiHalf.amp | PiGate.length (ns) | "
                     "ReadIn.power (dBm) | IQraw文件 | Rabi文件 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in alive:
            regs = r.get("wp_regs", {}) or {}
            lines.append(
                f"| {r['qubit']} | {fmt(r['wp_zpa'], 4)} | {fmt(r['wp_f10'], 6)} | "
                f"{fmt(r['wp_f21'], 6)} | {fmt(r['wp_fread'], 6)} | {fmt(r['wp_fc'], 6)} | "
                f"{fmt(r['wp_fc_read'], 6)} | {fmt(regs.get('PiGate.amp'), 6)} | "
                f"{fmt(regs.get('PiHalf.amp'), 6)} | "
                f"{fmt(regs.get('PiGate.length'), 4)} | "
                f"{fmt(regs.get('ReadIn.power'), 4)} | "
                f"{r['iqraw_best_index']} | {r['rabi_index']} |")
    else:
        lines.append("（无）")

    lines.append("\n## 三、单比特性质汇总（全部比特）\n")
    lines.append("| 比特 | 文件数 | 工作点f10 (GHz) | 谱测f10 (GHz) | 谱测fread (GHz) | "
                 "T2* (ns) | T2 (ns) | 失谐 (MHz) | Rabi峰谷落差 | 最优pi幅度 | IQraw SNR | "
                 "01判别距离 | IQraw文件 | Rabi文件 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        valid = r.get("Ramsey_df_valid")
        t2s = fmt(r.get("Ramsey_df_T2star"), 4) if valid else "未测得"
        t2 = fmt(r.get("Ramsey_df_T2"), 4) if valid else "—"
        dfm = fmt(r.get("Ramsey_df_detune_MHz"), 3) if valid else "—"
        spec = fmt(r.get("Spectroscopy_f10_meas"), 6)
        if r.get("Spectroscopy_conflict"):
            spec += " ⚠"
        lines.append(
            f"| {r['qubit']} | {r['n_files']} | {fmt(r['wp_f10'], 6)} | {spec} | "
            f"{fmt(r.get('S21_dis_fread_meas_amp0'), 6)} | {t2s} | {t2} | {dfm} | "
            f"{fmt(r['rabi_contrast'], 3)} | "
            f"{fmt(r['rabi_amp_opt'], 3)} | {fmt(r['iqraw_snr'], 3)} | "
            f"{fmt(r.get('S21_dis_distance01_max'), 4)} | "
            f"{r['iqraw_best_index']} | {r['rabi_index']} |")
    lines.append("\n> T2* 一列取工作点上那条 Ramsey df 记录拟合（阻尼余弦，FFT 主频初始化）；"
                 "拟合不成立（r²<0.3 或振荡幅度<0.05）时标为“未测得”。"
                 "Ramsey 延迟窗口 0–1000 ns，故 T2 ≥ 1 µs 时 T2 仅为下界。")
    lines.append("> 谱测 f10 取 P1(freq) 共振峰：优先选峰显著且贴近工作点 f10 的记录；"
                 "找不到时取最显著的一条并标 ⚠（表示该比特本次未获得干净共振）。")
    lines.append("> 01判别距离 = S21_dis 记录里 |0>/|1> 的 IQ 平面距离，越大读出分辨率越好。\n")

    lines.append("## 四、活比特可写入 LabRAD 的参数\n")
    if alive:
        lines.append("```python")
        lines.append("# 活比特寄存器快照（来自 20260822_device1 会话的工作点记录）")
        for r in alive:
            regs = r.get("wp_regs", {}) or {}
            lines.append(f"{r['qubit']}.regs.f10   = {fmt(r['wp_f10'], 8)}   "
                         f"# {fmt(r['wp_f21'], 8)} GHz")
            lines.append(f"{r['qubit']}.regs.f21   = {fmt(r['wp_f21'], 8)}")
            lines.append(f"{r['qubit']}.regs.fread = {fmt(r['wp_fread'], 8)}")
            lines.append(f"{r['qubit']}.regs.bias_z = {fmt(r['wp_zpa'], 4)}   # zpa")
            lines.append(f"{r['qubit']}.regs.fc    = {fmt(r['wp_fc'], 6)}   "
                         f"# 读出腔 fc_read={fmt(r['wp_fc_read'], 6)}")
            lines.append(f"{r['qubit']}.regs.PiGate.amp   = {fmt(regs.get('PiGate.amp'), 6)}")
            lines.append(f"{r['qubit']}.regs.PiHalf.amp  = {fmt(regs.get('PiHalf.amp'), 6)}")
            lines.append("")
        lines.append("```")
    else:
        lines.append("（无）")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="DataVault 单比特体检")
    ap.add_argument("--vault", required=True, help="DataVault session 目录")
    ap.add_argument("--start", type=int, default=None, help="起始序号（含）")
    ap.add_argument("--end", type=int, default=None, help="结束序号（含）")
    ap.add_argument("-o", "--out", default="out", help="输出目录")
    args = ap.parse_args()

    rows = analyze(args.vault, args.start, args.end)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out / "qubit_health.csv")
    write_md(rows, out / "qubit_health.md", args.vault, args.start, args.end)

    print(f"比特数: {len(rows)}   判活: {sum(1 for r in rows if r['alive'])}")
    for r in rows:
        print(f"  {r['qubit']:8s} iqraw_snr={fmt(r['iqraw_snr'],3):>8s} "
              f"norm={fmt(r['iqraw_norm_best'],3):>8s} rabi_swing={fmt(r['rabi_contrast'],3):>7s} "
              f"zpa={fmt(r['wp_zpa'],4):>6s} f10={fmt(r['wp_f10'],6):>9s} "
              f"f21={fmt(r['wp_f21'],6):>9s} fread={fmt(r['wp_fread'],6):>9s} "
              f"T2*={fmt(r.get('Ramsey_df_T2star'),4):>9s} "
              f"{'ALIVE' if r['alive'] else '-'}")
    print(f"\n报告: {out / 'qubit_health.md'}")


if __name__ == "__main__":
    main()

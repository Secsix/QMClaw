"""离线复现 LabRAD DataVault 数据集的实验图像（无需 LabRAD / Registry）。

用法示例：

    python plot_datavault.py --dir "D:\\DataVault\\LQHL.dir\\test.dir\\20260324.dir" \
        --qubit q3ld4 --datasets 342-350 --out "D:\\out\\plots" --formats png,svg,pdf

要点（都是踩过坑总结的，详见同目录 SKILL.md）：
  * lqms 自带的 local vault 初始化要读 LabRAD/Registry 认证，本机不可用；
    改为直接用 lv.dataset_loader_hdf5 读 HDF5，再手工重建 dc.DataLab。
  * ind/dep 表项必须同时是 dict（含 units 键）且有 values() 方法 -> 用 Header 类。
  * 参数字典里 q3ld4.* 要补一份 q.* 别名，否则 ramsey / dispersive_shift / IQ_raw
    查 q.f10 会 KeyError；别名只在内存里做，不改原始数据与配置。
  * 矢量导出：imshow / pcolormesh 的色标条会在 SVG/PDF 里内嵌位图，
    2D 图改用 contourf 才能整图纯矢量。
"""

import argparse
import os
import pathlib
import re
import traceback

import numpy as np

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import lqms.data_process.local_data_vault.local_vault as lv  # noqa: E402
from lqms.data_process import dataAnalysisCore as dc  # noqa: E402
from lqms.data_process import data_process as dp  # noqa: E402
from lqms.data_process import fitFunc  # noqa: E402
from lqms.data_process.analysis_util import readout_analysis as ra  # noqa: E402


class Header(dict):
    """DataLab 的 ind/dep 表项：既能像 dict 取键（makeLabel 要 units），又有 values()。"""

    def __init__(self, name, values, **kw):
        kw.setdefault("units", kw.get("unit", ""))
        kw.setdefault("unit", kw.get("units", ""))
        super().__init__(name=name, values=values, **kw)
        self._values = values

    def values(self):
        return self._values


class Ctx(object):
    """一次绘图任务的上下文。"""

    def __init__(self, root, out, session, qubit, formats):
        self.root = pathlib.Path(root)
        self.out = pathlib.Path(out)
        self.session = session
        self.qubit = qubit
        self.formats = formats

    # ---------- 数据读取 ----------
    def find_file(self, num):
        pats = ["%05d*.hdf5" % num, "%d*.hdf5" % num]
        for pat in pats:
            hit = sorted(self.root.glob(pat))
            if hit:
                return hit[0]
        raise FileNotFoundError("在 %s 找不到 %05d 的 hdf5" % (self.root, num))

    def build(self, num):
        path = self.find_file(num)
        ds = lv.dataset_loader_hdf5(str(path)[:-5])
        arr = np.asarray(ds)
        lab = dc.DataLab.__new__(dc.DataLab)
        lab._clear_data()

        n_ind = len(ds.indeps)
        lab.inds = [
            Header(d.label, arr[:, i], unit=d.unit, shape=d.shape, datatype=d.datatype)
            for i, d in enumerate(ds.indeps)
        ]
        lab.deps = [
            Header(d.label, arr[:, n_ind + i], unit=d.unit, legend=d.legend,
                   shape=d.shape, datatype=d.datatype)
            for i, d in enumerate(ds.deps)
        ]

        params = dict(ds.parameters)
        if self.qubit:
            for k, v in list(params.items()):
                if k.startswith(self.qubit + "."):
                    params["q." + k[len(self.qubit) + 1:]] = v

        lab.data = arr
        lab.dim = n_ind
        lab.dataset_name = ds.name
        lab.dataset_num = num
        lab.datasets_num = [num]
        lab.datasets_name = [ds.name]
        lab.data_shape = {num: arr.shape}
        lab.grid = n_ind > 1
        lab.session = list(self.session)
        lab.name_info = {"name": [self.qubit] if self.qubit else [],
                         "q_name": self.qubit, "units": {}}
        lab.dv_type = "local_file"
        lab.measure_info = params
        lab.measure_info.setdefault("units", {})
        lab.parameters = params
        return lab, ds, arr

    # ---------- 输出 ----------
    def save(self, num, label):
        saved = []
        for i, fignum in enumerate(plt.get_fignums()):
            fig = plt.figure(fignum)
            base = "%d_%s_%d" % (num, label, i)
            for fmt in self.formats:
                p = self.out / ("%s.%s" % (base, fmt))
                if fmt == "png":
                    fig.savefig(p, dpi=140, bbox_inches="tight")
                else:
                    fig.savefig(p, format=fmt, bbox_inches="tight")
                saved.append(p)
        return saved


def dep_values(lab, name=None, fallback=-2):
    """按名字取依赖量；取不到就退回倒数第二个列（P1 惯例）。"""
    if name:
        for d in lab.deps:
            if d["name"] == name:
                return np.asarray(d.values(), dtype=float).ravel()
    return np.asarray(lab.deps[fallback].values(), dtype=float).ravel()


def indep_values(lab, index=0):
    return np.asarray(lab.inds[index].values(), dtype=float).ravel()


# ============================ 各实验的画法 ============================

def draw_s21(ctx, lab, arr):
    """S21 扫频：归一化幅值(dB) + 相位，标出谐振凹点。"""
    x = indep_values(lab, 0)
    amp = dep_values(lab, "Amplitude", 0)
    phase = dep_values(lab, "Phase", 1)
    db = 20 * np.log10(np.abs(amp) / np.max(np.abs(amp)))
    idx = int(np.argmin(db))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(x, db, "o-", ms=4, color="tab:blue", label="|S21|")
    ax1.plot(x[idx], db[idx], "rv", ms=10,
             label="dip %.4f GHz (%.1f dB)" % (x[idx], db[idx]))
    ax1.set_ylabel("|S21| (dB, normalized)")
    ax1.set_title("S21  %s" % ctx.session)
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=9)
    ax2.plot(x, phase, "o-", ms=4, color="tab:orange", label="Phase")
    ax2.set_xlabel("freq (GHz)")
    ax2.set_ylabel("Phase (rad)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    print("    resonant dip %.6f GHz, %.2f dB" % (x[idx], db[idx]))


def draw_zpa2d(ctx, lab, arr):
    """S21zpa2d（2D）：左 freq-zpa 幅值图，右逐点拟合频率与误差。

    用 contourf 而不是 pcolormesh：matplotlib 的 pcolormesh 色标条在 SVG/PDF
    里会内嵌成位图，contourf 的色标是路径，整图保持矢量。
    """
    meta, data = dp.zpa2d(lab, do_plot=False)
    zpas = np.asarray(data[0], dtype=float).ravel()
    freqs_fit = np.asarray(data[2], dtype=float).ravel()
    freq_err = np.asarray(data[3], dtype=float).ravel()
    n_z = len(zpas)
    flat = dep_values(lab, "Amplitude", 0)
    n_f = flat.size // n_z
    amp = flat.reshape(n_f, n_z)
    freq_axis = np.unique(indep_values(lab, 0))

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(13.5, 4.6), gridspec_kw={"width_ratios": [1.3, 1]}
    )
    mesh = ax1.contourf(zpas, freq_axis, amp, levels=16, cmap="jet")
    ax1.plot(zpas, freqs_fit, "k--o", ms=4, lw=1.1, label="fit")
    ax1.set_xlabel("zpa (V)")
    ax1.set_ylabel("freq (GHz)")
    ax1.set_title("S21zpa2d  %s" % ctx.session)
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=9, loc="upper right")
    cb = fig.colorbar(mesh, ax=ax1)
    cb.set_label("Amplitude")

    ax2.plot(zpas, freq_err * 1e3, "o-", ms=4, color="tab:blue")
    ax2.set_xlabel("zpa (V)")
    ax2.set_ylabel("fitting error (MHz)")
    ax2.set_title("fitting error")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    print("    resonator freq (zpa=0) = %.5f GHz, max err %.3f MHz"
          % (np.interp(0.0, zpas, freqs_fit), np.abs(freq_err).max() * 1e3))


def draw_spectroscopy(ctx, lab, arr):
    """Spectroscopy：P1 随驱动频率，标出 find_peaks 找到的峰与参数表 f10。"""
    x = indep_values(lab, 0)
    y = dep_values(lab, "P1")

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(x, y, "o-", ms=3.5, color="tab:blue", label="P1")
    try:
        pk = fitFunc.find_peaks(x, y, s=0.05, do_plot=False)["peaks"]
        thr = y.min() + 0.5 * (y.max() - y.min())
        keep = [(a, b) for a, b in zip(pk[1], pk[2]) if b >= thr]
        if keep:
            xs, ys = zip(*keep)
            ax.plot(xs, ys, "r*", ms=13, label="peaks (lqms find_peaks)")
            for a, b in keep:
                ax.annotate("%.4f GHz" % a, (a, b), textcoords="offset points",
                            xytext=(0, 9), ha="center", fontsize=8, color="tab:red")
    except Exception:
        traceback.print_exc()

    f10 = lab.parameters.get("q.f10")
    if f10:
        ax.axvline(f10, ls="--", color="tab:green", lw=1,
                   label="q.f10 = %.5f GHz" % f10)

    ax.set_xlabel("freq (GHz)")
    ax.set_ylabel("P1")
    ax.set_title("Spectroscopy  %s" % ctx.session)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()


def draw_pipulse(ctx, lab, arr):
    """PiPulse：P1 随 pi 脉冲幅度振荡，标出极大/极小点。"""
    x = indep_values(lab, 0)
    y = dep_values(lab, "P1")

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(x, y, "o-", ms=4, color="tab:blue", label="P1")
    try:
        r = fitFunc.find_peaks(x, y, s=0.02, do_plot=False)
        ax.plot(r["peaks"][1], r["peaks"][2], "r*", ms=12, label="peaks")
        ax.plot(r["dips"][1], r["dips"][2], "gv", ms=8, label="dips")
        mp = r["max point"]
        ax.plot(mp[0], mp[1], "ko", ms=10, mfc="none",
                label="max point %.4f (P1=%.3f)" % (mp[0], mp[1]))
        print("    pi amplitude (first max) = %.4f" % mp[0])
    except Exception:
        traceback.print_exc()
    ax.set_xlabel("piAmp")
    ax.set_ylabel("P1")
    ax.set_title("PiPulse  %s" % ctx.session)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()


def draw_iqraw(ctx, lab, arr):
    """IQraw：优先用 lqms 的 IQ_raw（双模 I/X 分离度、SNR、可见度）。

    单模数据集（依赖量只有 Is/Qs 两列，如 'Is | 0'）会让 lqms 越界报错，
    这时退回自绘 I/Q 散点。
    """
    try:
        ra.IQ_raw(lab, do_plot=True, collect=True)
        return
    except Exception as exc:
        print("    [warn] lqms IQ_raw 失败(%s)，改用自绘 I/Q 散点" % type(exc).__name__)

    names = [d["name"] for d in lab.deps]
    groups = {}
    for i, n in enumerate(names):
        parts = [p.strip() for p in n.split("|")]
        if len(parts) == 2 and parts[0].lower() in ("is", "qs"):
            groups.setdefault(parts[1], {})[parts[0].lower()] = i
    if not groups:
        raise RuntimeError("无法识别的 IQraw 依赖量: %s" % names)

    fig, ax = plt.subplots(figsize=(6, 6))
    for mode, cols in sorted(groups.items()):
        if "is" not in cols or "qs" not in cols:
            continue
        i_val = np.asarray(lab.deps[cols["is"]].values(), dtype=float).ravel()
        q_val = np.asarray(lab.deps[cols["qs"]].values(), dtype=float).ravel()
        ax.scatter(i_val, q_val, s=4, alpha=0.5, label="|%s>" % mode)
    ax.set_xlabel("I")
    ax.set_ylabel("Q")
    ax.set_title("IQraw  %s" % ctx.session)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    print("    单模 I/Q 散点: %s" % ", ".join(sorted(groups)))


def fit_t1(delay, p1):
    """对一条衰减曲线做单指数拟合，返回 T1（与 delay 同一时间单位）。"""
    mask = p1 > 1e-6
    if mask.sum() < 3:
        return np.nan
    x = np.asarray(delay, float)[mask]
    y = np.log(np.asarray(p1, float)[mask])
    A = np.vstack([x, np.ones_like(x)]).T
    slope, _ = np.linalg.lstsq(A, y, rcond=None)[0]
    if slope >= 0:
        return np.nan
    t1 = -1.0 / slope
    # 扫描窗口内几乎不衰减的切片会给出离谱的大数，判为无效
    if not np.isfinite(t1) or t1 > 5 * np.max(np.abs(x)):
        return np.nan
    return t1


def draw_t1(ctx, lab, arr):
    """T1：一维交给 lqms；二维（zpa × delay）lqms 离线跑不了，改自绘 + 逐 zpa 拟合。

    二维的自绘内容与 lqms 原意一致：P1(zpa, delay) 热图 + 各 zpa 处的 T1。
    """
    if len(lab.inds) < 2:
        dp.T1(lab, do_plot=True)
        return

    z = np.asarray(lab.inds[0].values(), dtype=float).ravel()
    delay = np.asarray(lab.inds[1].values(), dtype=float).ravel()
    p1 = dep_values(lab, "P1 | I", 0)
    zu = np.unique(z)
    du = np.unique(delay)
    zi = np.searchsorted(zu, z)
    di = np.searchsorted(du, delay)
    grid = np.full((len(zu), len(du)), np.nan)
    grid[zi, di] = p1

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(13.5, 4.8), gridspec_kw={"width_ratios": [1.3, 1]}
    )
    # contourf 的 Z 形状是 (len(y), len(x))，所以要转置成 (delay, zpa)
    mesh = ax1.contourf(zu, du, grid.T, levels=16, cmap="jet")
    ax1.set_xlabel("zpa (V)")
    ax1.set_ylabel("delay")
    ax1.set_title("T1  P1(zpa, delay)  %s" % ctx.session)
    cb = fig.colorbar(mesh, ax=ax1)
    cb.set_label("P1")

    t1s = [fit_t1(du, row) for row in grid]
    ax2.plot(zu, t1s, "o-", ms=4, color="tab:blue")
    ax2.set_xlabel("zpa (V)")
    ax2.set_ylabel("T1 (delay 轴同单位)")
    ax2.set_title("T1 vs zpa")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    good = [t for t in t1s if np.isfinite(t)]
    if good:
        print("    T1(zpa) 中位数 %.2f，最大 %.2f" % (np.median(good), np.max(good)))


def draw_generic(ctx, lab, arr):
    """兜底：第一个依赖量 vs 第一个独立量。"""
    x = indep_values(lab, 0)
    y = dep_values(lab, None, 0)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(x, y, "o-", ms=4)
    ax.set_xlabel(lab.inds[0]["name"])
    ax.set_ylabel(lab.deps[0]["name"])
    ax.set_title("%s  %s" % (lab.dataset_name, ctx.session))
    ax.grid(alpha=0.3)
    fig.tight_layout()


# 实验名 -> 画法。键是文件名里的实验名（去掉编号前缀和 qubit 前缀）。
DRAWERS = {
    "s21": draw_s21,
    "s21zpa2d": draw_zpa2d,
    "spectroscopy": draw_spectroscopy,
    "pipulse": draw_pipulse,
}


def pick_drawer(experiment):
    """按实验名挑画法；先精确匹配，再退回 lqms 自带分析入口。"""
    key = experiment.strip().lower().replace(" ", "")
    if key in DRAWERS:
        return DRAWERS[key], "custom"

    if key == "iqraw":
        return draw_iqraw, "lqms"
    if key.startswith("ramsey"):
        return (lambda c, l, a: dp.ramsey(l, do_plot=True)), "lqms"
    if key == "s21_dis":
        return (lambda c, l, a: dp.dispersive_shift(l, do_plot=True)), "lqms"
    if key == "t1":
        return draw_t1, "lqms"
    return draw_generic, "generic"


def experiment_of(path, qubit):
    """从文件名解析实验名：'00342 - q3ld4%c S21.hdf5' -> 'S21'。"""
    stem = path.stem
    stem = re.sub(r"^\d+\s*[-_]?\s*", "", stem)
    if qubit:
        stem = re.sub(r"^%s\s*%%?c\s*" % re.escape(qubit), "", stem)
    return stem.strip()


def parse_datasets(spec, root):
    """'342-350' / '342,345' / 'all' -> 数据集编号列表。"""
    if not spec or spec.lower() == "all":
        nums = sorted(int(p.name[:5]) for p in root.glob("*.hdf5"))
        return nums
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def merge_pdfs(out):
    try:
        from pypdf import PdfWriter
    except ImportError:
        print("  (pypdf 未安装，跳过合并)")
        return None
    pdfs = sorted(out.glob("*.pdf"))
    if not pdfs:
        return None
    w = PdfWriter()
    for p in pdfs:
        w.append(str(p))
    target = out / "all_plots.pdf"
    with open(target, "wb") as f:
        w.write(f)
    print("  合订:", target, len(w.pages), "页")
    return target


def main():
    ap = argparse.ArgumentParser(description="离线复现 DataVault 数据集的实验图像")
    ap.add_argument("--dir", required=True, help="DataVault 数据集目录（含 hdf5）")
    ap.add_argument("--out", default="plots", help="输出目录")
    ap.add_argument("--session", default="", help="session 名，如 LQHL/test/20260324")
    ap.add_argument("--qubit", default="", help="qubit 名，如 q3ld4（用于参数别名）")
    ap.add_argument("--datasets", default="all", help="编号，如 342-350 或 342,345")
    ap.add_argument("--formats", default="png,svg,pdf", help="png,svg,pdf 任意组合")
    args = ap.parse_args()

    root = pathlib.Path(args.dir)
    if not root.is_dir():
        raise SystemExit("目录不存在: %s" % root)

    session = args.session or "/".join(root.parts[-3:])
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    ctx = Ctx(root, pathlib.Path(args.out), session.split("/"), args.qubit, formats)
    ctx.out.mkdir(parents=True, exist_ok=True)

    nums = parse_datasets(args.datasets, root)
    print("数据目录: %s" % root)
    print("session  : %s" % session)
    print("数据集   : %s" % nums)

    results = {}
    for num in nums:
        plt.close("all")
        try:
            path = ctx.find_file(num)
        except FileNotFoundError as e:
            print("\n=== %d === 跳过: %s" % (num, e))
            results[num] = ("MISSING", [])
            continue
        exp = experiment_of(path, args.qubit)
        drawer, kind = pick_drawer(exp)
        print("\n=== %d  %s  [%s / %s] ===" % (num, path.name, exp, kind))
        try:
            lab, ds, arr = ctx.build(num)
            drawer(ctx, lab, arr)
            if not plt.get_fignums():
                print("  [warn] 没有产生图窗")
                results[num] = (exp, [])
                continue
            saved = ctx.save(num, exp.replace(" ", ""))
            for p in saved:
                print("  saved", p)
            results[num] = (exp, saved)
        except Exception:
            traceback.print_exc()
            results[num] = (exp, [])

    print("\n=== 汇总 ===")
    bad = 0
    for num in sorted(results):
        exp, saved = results[num]
        if not saved:
            bad += 1
        print("  %4d  %-16s %s" % (num, exp, "OK x%d" % len(saved) if saved else "FAILED"))
    print("  失败 %d / %d" % (bad, len(results)))

    if "pdf" in formats:
        merge_pdfs(ctx.out)


if __name__ == "__main__":
    main()

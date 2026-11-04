"""单比特体检：基于 DataVault 离线数据的指标计算。

每个实验类型的指标：
  IQraw      → IQraw 归一化分离度 / 单发 SNR
  PiPulse    → Rabi 振荡拟合（最佳 pi 幅度、对比度、周期）
  PiPulse df → 失谐扫描（共振 df）
  Ramsey df  → T2* 与失谐
  Spectroscopy→ 比特频率 f10（实测）
  S21_dis    → 读出谐振腔频率 fread（实测）与 |0>/|1> 分辨率
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import curve_fit


# ------------------------------------------------------------------ IQraw
def iqraw_metrics(cols: dict) -> dict:
    """IQraw：两态 IQ 云团的分离度。

    Is|Q0>, Qs|Q0> 为 |0> 态，Is|Q1>, Qs|Q1> 为 |1> 态。
      sep       : 两个复平面的平均中心距离（ADC counts）
      snr       : 单发 SNR = sep / sqrt((σ0²+σ1²)/2)
      norm      : 归一化分离度 = sep / (|z0|+|z1|)      ← 判活主指标
      fidelity  : 最优线性判别的单发判别正确率
    """
    need = ("Is | I", "Qs | I", "Is | X", "Qs | X")
    if any(k not in cols for k in need):
        return {}
    z0 = np.asarray(cols["Is | I"], float) + 1j * np.asarray(cols["Qs | I"], float)
    z1 = np.asarray(cols["Is | X"], float) + 1j * np.asarray(cols["Qs | X"], float)
    m0, m1 = z0.mean(), z1.mean()
    sep = float(abs(m1 - m0))
    s0 = float(np.sqrt(np.mean(np.abs(z0 - m0) ** 2)))
    s1 = float(np.sqrt(np.mean(np.abs(z1 - m1) ** 2)))
    snr = sep / np.sqrt((s0 ** 2 + s1 ** 2) / 2) if (s0 + s1) > 0 else 0.0
    amp = abs(m0) + abs(m1)
    norm = sep / amp if amp > 0 else 0.0
    # 最优判别方向（两高斯等方差）：沿 m1-m0 投影
    d = m1 - m0
    if abs(d) > 0:
        u = d / abs(d)
        p0 = np.real(z0 * np.conj(u)).mean()
        p1 = np.real(z1 * np.conj(u)).mean()
        sd = np.sqrt((np.real(z0 * np.conj(u)).var() + np.real(z1 * np.conj(u)).var()) / 2)
        fid = 0.5 * (1 + math.erf(abs(p1 - p0) / (2 * sd))) if sd > 0 else 0.5
    else:
        fid = 0.5
    return {"sep": sep, "snr": float(snr), "norm": float(norm),
            "fidelity": float(fid), "abs_z0": float(abs(m0)), "abs_z1": float(abs(m1))}


# ------------------------------------------------------------------ Rabi
def _rabi(x, a, b, f, ph, tau):
    return a + b * np.cos(2 * np.pi * f * x + ph) * np.exp(-x / max(tau, 1e-6))


RABI_SWING_THRESH = 0.15    # Rabi 振荡的 P1 峰谷落差下限
RABI_SIGMA_K = 4.0          # 峰谷落差 / 噪声 的显著性倍数


def rabi_fit(cols: dict) -> dict:
    """PiPulse：判断 P1(piAmp) 是否呈现 Rabi 振荡。

    主判据（与拟合参数无关，避免噪声数据拟合发散）：
      * 出现内部极大值（峰），且峰后有回落（谷不在数据末端）
      * 峰谷落差 ≥ RABI_SWING_THRESH，且 ≥ RABI_SIGMA_K × 噪声σ（二阶差分 MAD 估计）
    辅助：阻尼余弦拟合给出周期与最优 pi 幅度。
    """
    if "piAmp" not in cols or "P1" not in cols:
        return {}
    x = np.asarray(cols["piAmp"], float)
    y = np.asarray(cols["P1"], float)
    n = len(x)
    if n < 6:
        return {}
    step = float(np.median(np.diff(x)))
    span = float(x.max() - x.min())

    # 噪声估计：三阶差分的 MAD（三阶差分对光滑曲线趋于 0，只剩噪声）
    if n >= 6:
        d3 = y[:-3] - 3 * y[1:-2] + 3 * y[2:-1] - y[3:]
        sigma = 1.4826 * float(np.median(np.abs(d3 - np.median(d3)))) / np.sqrt(20.0)
    else:
        sigma = 0.0

    out = {"ok": False, "sigma": sigma, "p1_min": float(y.min()), "p1_max": float(y.max()),
           "amp_range": [float(x.min()), float(x.max())], "peak_idx": None, "trough_idx": None,
           "swing": 0.0, "score": 0.0, "amp_opt": None, "period": None}

    # 找内部峰 + 峰后谷（允许平台顶：y[i] 严格大于一侧、≥ 另一侧）
    best = None
    for i in range(1, n - 1):
        up = y[i] > y[i - 1]
        dn = y[i] >= y[i + 1]
        up2 = y[i] >= y[i - 1]
        dn2 = y[i] > y[i + 1]
        if not ((up and dn) or (up2 and dn2)):
            continue
        j = i + int(np.argmin(y[i + 1:]))
        if j >= n - 1:                     # 谷必须不在数据末端
            continue
        swing = float(y[i] - y[j])
        score = swing / sigma if sigma > 0 else 0.0
        if swing < RABI_SWING_THRESH or score < RABI_SIGMA_K:
            continue
        if best is None or score > best["score"]:
            best = {"peak_idx": i, "trough_idx": j, "swing": swing, "score": score}

    # 阻尼余弦拟合（辅助信息）
    p0 = [float(y.min()), float(y.max() - y.min()), 1.0 / max(span, 1e-9), 0.0, 1e9]
    bounds = ([-1.0, 0.0, 0.0, -10 * np.pi, 1.0], [2.0, 1.0, 20.0, 10 * np.pi, 1e9])
    try:
        popt, _ = curve_fit(_rabi, x, y, p0=p0, bounds=bounds, maxfev=40000)
        a, b, f, ph, tau = popt
        xs = np.linspace(x.min(), x.max(), 4001)
        out["fit_amp_opt"] = float(xs[int(np.argmax(_rabi(xs, *popt)))])
        out["period"] = float(1.0 / f) if f > 0 else float("nan")
        out["fit_contrast"] = float(abs(b))
    except Exception:
        pass

    if best is None:
        return out
    out.update(best)
    out["ok"] = True
    out["amp_opt"] = float(x[best["peak_idx"]])
    out["full_osc"] = True
    return out


# ------------------------------------------------------------------ Ramsey
def _ramsey(x, a, b, f, ph, tau):
    return a + b * np.exp(-x / max(tau, 1e-6)) * np.cos(2 * np.pi * f * x + ph)


def ramsey_fit(cols: dict) -> dict:
    """Ramsey df：P1(delay) 阻尼余弦拟合 → T2* 与失谐。

    初值用 FFT 主频（否则多参数拟合常退化到错误频率）。
    """
    if "delay" not in cols or "P1" not in cols:
        return {}
    x = np.asarray(cols["delay"], float)
    y = np.asarray(cols["P1"], float)
    if len(x) < 6:
        return {}
    dt = float(np.median(np.diff(x)))
    span = float(x.max() - x.min())
    # 频率范围：周期 ≤ 扫描跨度，≥ 4 个采样步长
    lo, hi = 1.0 / max(span, 1e-9), 1.0 / (4.0 * dt)
    # FFT 主频作初值
    yc = y - y.mean()
    spec = np.abs(np.fft.rfft(yc))
    freqs = np.fft.rfftfreq(len(yc), d=dt)
    k = int(np.argmax(spec[1:])) + 1
    f0 = float(min(max(freqs[k], lo), hi))
    p0 = [float(y.min()), float(y.max() - y.min()), f0, 0.0, span]
    bounds = ([-1.0, -1.0, lo, -10 * np.pi, 1.0], [2.0, 1.0, hi, 10 * np.pi, 1e5])
    try:
        popt, _ = curve_fit(_ramsey, x, y, p0=p0, bounds=bounds, maxfev=40000)
    except Exception:
        return {"ok": False}
    a, b, f, ph, tau = popt
    # T2* = 1 / sqrt((1/T2)^2 + (pi*df)^2)
    t2s = 1.0 / np.sqrt((1.0 / max(tau, 1e-9)) ** 2 + (np.pi * f) ** 2)
    ss_const = float(np.sum((y - y.mean()) ** 2))
    ss_fit = float(np.sum((_ramsey(x, *popt) - y) ** 2))
    r2 = 1 - ss_fit / ss_const if ss_const > 0 else 0.0
    return {"ok": True, "T2": float(tau), "T2star": float(t2s),
            "detune_MHz": float(f * 1e3 / 2.0), "osc_freq_MHz": float(f * 1e3),
            "contrast": float(abs(b)), "freq_Hz": float(f), "r2": float(r2),
            "period_ns": float(1.0 / f) if f > 0 else float("nan"),
            "delay_span": [float(x.min()), float(x.max())],
            "valid": bool(r2 >= 0.3 and abs(b) >= 0.05)}


# ------------------------------------------------------------------ 频率峰/谷
def _refine_peak(x, y, i, kind="max"):
    """在极值点附近做抛物线拟合精修。"""
    if i <= 0 or i >= len(x) - 1:
        return float(x[i])
    try:
        c = np.polyfit(x[i - 1:i + 2], y[i - 1:i + 2], 2)
        if abs(c[0]) < 1e-18:
            return float(x[i])
        dx = -c[1] / (2 * c[0])
        if abs(dx) > (x[i + 1] - x[i - 1]):
            return float(x[i])
        return float(x[i] + dx)
    except Exception:
        return float(x[i])


def spectroscopy_peak(cols: dict) -> dict:
    """Spectroscopy：P1(freq) 峰 → 比特频率。"""
    if "freq" not in cols or "P1" not in cols:
        return {}
    f = np.asarray(cols["freq"], float)
    y = np.asarray(cols["P1"], float)
    i = int(np.argmax(y))
    return {"f10_meas": _refine_peak(f, y, i), "p1_peak": float(y[i]),
            "p1_base": float(np.median(y)), "freq_span": [float(f.min()), float(f.max())]}


def s21_dip(cols: dict) -> dict:
    """S21_dis：|0> 态幅度谷 → 读出腔频率；distance|01 → 分辨率。"""
    if "freq" not in cols:
        return {}
    f = np.asarray(cols["freq"], float)
    out = {"freq_span": [float(f.min()), float(f.max())]}
    for key, tag in (("Amplitude | 0", "amp0"), ("Amplitude | 1", "amp1")):
        if key in cols:
            y = np.asarray(cols[key], float)
            i = int(np.argmin(y))
            out[f"fread_meas_{tag}"] = _refine_peak(f, y, i, kind="min")
            out[f"amp_{tag}_min"] = float(y[i])
    if "distance | 01" in cols:
        d = np.asarray(cols["distance | 01"], float)
        out["distance01_max"] = float(d.max())
        out["distance01_at"] = float(f[int(np.argmax(d))])
    return out


def pipulse_df_scan(cols: dict) -> dict:
    """PiPulse df：失谐扫描峰 → 共振失谐。"""
    if "df" not in cols or "P1" not in cols:
        return {}
    x = np.asarray(cols["df"], float)
    y = np.asarray(cols["P1"], float)
    i = int(np.argmax(y))
    return {"df_peak": _refine_peak(x, y, i), "p1_peak": float(y[i])}


METRIC_BY_EXP = {
    "IQraw": iqraw_metrics,
    "PiPulse": rabi_fit,
    "PiPulse df": pipulse_df_scan,
    "Ramsey df": ramsey_fit,
    "Spectroscopy": spectroscopy_peak,
    "S21_dis": s21_dip,
}

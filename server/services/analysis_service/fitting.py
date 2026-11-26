"""按实验种类拟合数据集，产出状态机认的物理量。

为什么放在微服务里
------------------
以前「Ramsey 该提什么参数、T1 用什么模型、XEB 的 p 怎么折成门保真度」这些口径写在
技能文档和一个叫 ``qter.fitData`` 的厂商方法里：技能文档会过期，厂商方法只在装了 LQCS 的
机器上存在。换一台只有 qiskit 的机器，同一批数据就没人能拟合。

现在口径在这一个文件里：

- ``analysis_service`` 的 ``POST /fit {dataset_id, kind}`` 直接调 :func:`analyse`；
- 双引擎：``engine="lqcs"`` 走厂商 ``fitData``（有 LQCS 才有），``engine="scipy"`` 走本模块，
  ``engine="auto"``（默认）先 lqcs 后 scipy；
- 技能只记「这个 kind 现在给什么物理量、门限是多少」，不记模型和代码。

拟合模型（都是最小可解释版本，参数名即物理量）
------------------------------------------------
=============  ==========================================  ==========================
kind           模型                                        产出
=============  ==========================================  ==========================
t1             A·exp(-t/T1)+c                              t1_us
ramsey 族      A·exp(-(t/T2)^p)·cos(2πft+φ)+c              t2_star_us、ramsey_freq
t2_echo 族     A·exp(-(t/T2e)^p)+c                         t2_echo_us
spectroscopy   洛伦兹峰/谷                                 f10、fwhm
s21            洛伦兹谷（谐振腔）                          f0、dip_depth
xeb            A·p^m+c                                     xeb_p、xeb_gate_fidelity
iqraw          双高斯（I 轴两峰）                          contrast、dmu_sig
piamp/rabi     正弦 A·sin(2πft+φ)+c                        pi_amplitude
drag_calibrate 抛物线 a·x²+bx+c                             drag_delta（极值位置）
=============  ==========================================  ==========================

约定
----
1. 拟合失败就 ``ok=False`` 并给 ``reason``，**不拿初值冒充结果**。
2. ``fit`` 为真才写 ``metrics``；``r2`` 一并返回，低于 0.5 时 ``warnings`` 里提示「拟合质量差」。
3. 不 import 厂商模块（``engine="lqcs"`` 分支里才延迟 import，且失败退 scipy）。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

#: 拟合质量提示线：低于它仍然返回结果，但带 warning（不是失败）
R2_WARN_THRESHOLD = 0.5

def gate_fidelity_from_p(p: float) -> float:
    """1 比特：``F = 1 - (1-p)/2``（``p`` 是每周期存活率）。"""
    p = float(p)
    return 1.0 - (1.0 - p) / 2.0


# ── 数据整形 ──────────────────────────────────────────────────────────────


def extract_series(data: Any) -> Tuple[np.ndarray, np.ndarray]:
    """从数据集里抽出 ``(x, y)``。

    支持：``(n, 2+)`` 的 ndarray（第一列 x、第二列 y）、``{"x": ..., "y": ...}``、
    一维 ndarray（下标作 x）。
    """
    if data is None:
        raise ValueError("数据集为空")
    if isinstance(data, dict):
        xs = data.get("x")
        ys = data.get("y")
        if xs is None or ys is None:
            raise ValueError("数据集字典需要 x / y 两列")
        return _as_float(xs), _as_float(ys)
    array = np.asarray(data, dtype=object)
    if array.ndim == 1:
        return np.arange(array.shape[0], dtype=float), _as_float(array)
    if array.ndim != 2:
        raise ValueError(f"数据集维度不支持：{array.ndim} 维")
    if array.shape[1] < 2:
        return np.arange(array.shape[0], dtype=float), _as_float(array[:, 0])
    return _as_float(array[:, 0]), _as_float(array[:, 1])


def _as_float(values: Any) -> np.ndarray:
    array = np.asarray(values)
    try:
        return array.astype(float)
    except (TypeError, ValueError):
        return np.array([float(v) for v in array.ravel()], dtype=float)


def _mask(x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    good = np.isfinite(x) & np.isfinite(y)
    return x[good], y[good]


def _r2(y: np.ndarray, y_fit: np.ndarray) -> float:
    ss_res = float(np.sum((y - y_fit) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if ss_tot <= 0:
        return 1.0 if ss_res <= 0 else 0.0
    return 1.0 - ss_res / ss_tot


def _curve_fit(
    model: Callable[..., np.ndarray],
    x: np.ndarray,
    y: np.ndarray,
    p0: Sequence[float],
    bounds: Tuple[Sequence[float], Sequence[float]],
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """统一用 scipy 的 ``curve_fit``；不可用或失败返回 None（由调用方决定怎么报）。"""
    try:
        from scipy.optimize import curve_fit as _cf  # 延迟 import：没装 scipy 也能 import 本模块
    except Exception:
        return None
    try:
        popt, _pcov = _cf(model, x, y, p0=list(p0), maxfev=20000, bounds=bounds)
        return np.asarray(popt, dtype=float), np.asarray(model(x, *popt), dtype=float)
    except Exception:
        return None


def _width_half(x: np.ndarray, y: np.ndarray, dip: bool = True) -> float:
    """用半高（或半深）宽度粗估 FWHM，作为洛伦兹拟合失败时的兜底。"""
    if y.size < 3:
        return 0.0
    base = float(np.max(y) if dip else np.min(y))
    peak = float(np.min(y) if dip else np.max(y))
    half = (base + peak) / 2.0
    if dip:
        idx = np.where(y <= half)[0]
    else:
        idx = np.where(y >= half)[0]
    if idx.size < 2:
        return 0.0
    return float(abs(x[idx[-1]] - x[idx[0]]))


# ── 模型 ──────────────────────────────────────────────────────────────────


def _exp_decay(x, amp, tau, offset):
    return amp * np.exp(-x / tau) + offset


def _stretched_exp(x, amp, tau, power, offset):
    return amp * np.exp(-np.power(np.abs(x) / tau, power)) + offset


def _ramsey(x, amp, tau, power, freq, phase, offset):
    return amp * np.exp(-np.power(np.abs(x) / tau, power)) * np.cos(2 * np.pi * freq * x + phase) + offset


def _lorentzian(x, amp, center, width, offset):
    return amp * (width ** 2) / ((x - center) ** 2 + width ** 2) + offset


def _sinusoid(x, amp, freq, phase, offset):
    return amp * np.sin(2 * np.pi * freq * x + phase) + offset


def _xeb_decay(x, amp, p, offset):
    return amp * np.power(np.clip(p, 1e-6, 1.0), x) + offset


def _quadratic(x, a, b, c):
    return a * x ** 2 + b * x + c


# ── 各 kind 的拟合 ────────────────────────────────────────────────────────


def _fit_t1(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    amp0 = float(np.max(y) - np.min(y)) or 1.0
    p0 = (amp0, max(float(x.max() - x.min()) / 3.0, 1e-9), float(np.min(y)))
    bounds = ([0.0, 1e-12, -np.inf], [np.inf, np.inf, np.inf])
    result = _curve_fit(_exp_decay, x, y, p0, bounds)
    if result is None:
        return {"ok": False, "reason": "指数拟合未收敛（scipy 不可用或初值太差）"}
    popt, y_fit = result
    amp, tau, offset = (float(v) for v in popt)
    return {
        "ok": True,
        "model": "A·exp(-t/T1)+c",
        "params": {"amplitude": amp, "t1_us": tau, "offset": offset},
        "metrics": {"t1_us": round(tau, 6)},
        "r2": _r2(y, y_fit),
    }


def _fit_ramsey(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    span = float(x.max() - x.min()) or 1.0
    amp0 = float(np.max(y) - np.min(y)) / 2.0 or 1.0
    # 频率初值取数据自身的主频（省得靠猜）
    freq0 = 1.0 / max(span, 1e-9) * 2.0
    try:
        yf = np.fft.rfft(y - np.mean(y))
        freqs = np.fft.rfftfreq(y.size, d=span / max(y.size - 1, 1))
        freq0 = float(abs(freqs[int(np.argmax(np.abs(yf)))]) or freq0)
    except Exception:
        pass
    p0 = (amp0, max(span / 2.0, 1e-9), 1.0, freq0, 0.0, float(np.mean(y)))
    bounds = ([0.0, 1e-12, 0.2, 0.0, -np.pi, -np.inf], [np.inf, np.inf, 4.0, np.inf, np.pi, np.inf])
    result = _curve_fit(_ramsey, x, y, p0, bounds)
    if result is None:
        return {"ok": False, "reason": "Ramsey 模型未收敛（scipy 不可用或初值太差）"}
    popt, y_fit = result
    amp, tau, power, freq, phase, offset = (float(v) for v in popt)
    return {
        "ok": True,
        "model": "A·exp(-(t/T2)^p)·cos(2πft+φ)+c",
        "params": {
            "amplitude": amp,
            "t2_us": tau,
            "stretch": power,
            "ramsey_freq": freq,
            "phase": phase,
            "offset": offset,
        },
        "metrics": {"t2_star_us": round(tau, 6), "ramsey_freq": round(freq, 9)},
        "r2": _r2(y, y_fit),
    }


def _fit_t2_echo(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    amp0 = float(np.max(y) - np.min(y)) or 1.0
    p0 = (amp0, max(float(x.max() - x.min()) / 3.0, 1e-9), 1.0, float(np.min(y)))
    bounds = ([0.0, 1e-12, 0.2, -np.inf], [np.inf, np.inf, 4.0, np.inf])
    result = _curve_fit(_stretched_exp, x, y, p0, bounds)
    if result is None:
        return {"ok": False, "reason": "T2 echo 模型未收敛（scipy 不可用或初值太差）"}
    popt, y_fit = result
    amp, tau, power, offset = (float(v) for v in popt)
    return {
        "ok": True,
        "model": "A·exp(-(t/T2e)^p)+c",
        "params": {"amplitude": amp, "t2_echo_us": tau, "stretch": power, "offset": offset},
        "metrics": {"t2_echo_us": round(tau, 6)},
        "r2": _r2(y, y_fit),
    }


def _fit_spectroscopy(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """扫频：默认找谷（dip）；若数据更像峰（峰在中间且高于两端），改找峰。"""
    center0 = float(x[int(np.argmin(y))])
    dip = True
    edges = np.concatenate([y[: max(1, y.size // 10)], y[-max(1, y.size // 10):]])
    if float(np.min(y)) > float(np.mean(edges)):
        center0 = float(x[int(np.argmax(y))])
        dip = False
    amp0 = float(np.min(y) - np.max(y)) if dip else float(np.max(y) - np.min(y))
    span = float(x.max() - x.min()) or 1.0
    p0 = (amp0, center0, span / 20.0, float(np.mean(edges)))
    bounds = ([-np.inf, float(x.min()), 1e-12, -np.inf], [np.inf, float(x.max()), np.inf, np.inf])
    result = _curve_fit(_lorentzian, x, y, p0, bounds)
    if result is None:
        return {
            "ok": True,
            "model": "argmin/argmax（洛伦兹未收敛时的兜底）",
            "params": {"center": center0, "fwhm_est": _width_half(x, y, dip=dip)},
            "metrics": {"f10": round(center0, 9), "fwhm": round(_width_half(x, y, dip=dip), 9)},
            "r2": 0.0,
            "warnings": ["洛伦兹拟合未收敛，f10 取自极值点位置"],
        }
    popt, y_fit = result
    amp, center, width, offset = (float(v) for v in popt)
    return {
        "ok": True,
        "model": "洛伦兹（amp·w²/((x-c)²+w²)+offset）",
        "params": {"amplitude": amp, "center": center, "hwhm": width, "offset": offset, "dip": dip},
        "metrics": {"f10": round(center, 9), "fwhm": round(2.0 * width, 9)},
        "r2": _r2(y, y_fit),
    }


def _fit_xeb(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    amp0 = float(np.max(y) - np.min(y)) or 1.0
    p0 = (amp0, 0.99, float(np.min(y)))
    bounds = ([0.0, 1e-6, -np.inf], [np.inf, 1.0, np.inf])
    result = _curve_fit(_xeb_decay, x, y, p0, bounds)
    if result is None:
        return {"ok": False, "reason": "XEB 衰减未收敛（线性化失败）"}
    popt, y_fit = result
    amp, p, offset = (float(v) for v in popt)
    fidelity = gate_fidelity_from_p(p)
    return {
        "ok": True,
        "model": "A·p^m+c",
        "params": {"amplitude": amp, "decay_per_cycle": p, "offset": offset},
        "metrics": {"xeb_p": round(p, 6), "xeb_gate_fidelity": round(fidelity, 6)},
        "r2": _r2(y, y_fit),
        "warnings": ["xeb_gate_fidelity 由 1 比特关系 F = 1-(1-p)/2 折算"],
    }


def _fit_iqraw(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """IQ 单发：拿 I 轴直方图做双高斯，算两峰间距 / 宽度（separ'' → contrast）。"""
    values = y if y.size else x
    values = values[np.isfinite(values)]
    if values.size < 8:
        return {"ok": False, "reason": "样本太少，无法做双高斯"}
    lo, hi = float(np.min(values)), float(np.max(values))
    if hi <= lo:
        return {"ok": False, "reason": "数值退化（全部相同）"}
    # 以中位数分成两簇，各取均值与标准差
    mid = float(np.median(values))
    low = values[values <= mid]
    high = values[values > mid]
    if low.size < 2 or high.size < 2:
        return {"ok": False, "reason": "两簇样本不足"}
    mu0, mu1 = float(np.mean(low)), float(np.mean(high))
    sd0, sd1 = float(np.std(low, ddof=1)), float(np.std(high, ddof=1))
    separation = abs(mu1 - mu0) / max((sd0 + sd1) / 2.0, 1e-12)
    contrast = min(1.0, separation / 2.0)
    return {
        "ok": True,
        "model": "双高斯（按中位数分簇估计）",
        "params": {"mu0": mu0, "mu1": mu1, "sd0": sd0, "sd1": sd1},
        "metrics": {
            "separation": round(separation, 6),
            "contrast": round(contrast, 6),
            "dmu_sig": round(separation, 6),
        },
        "r2": 0.0,
        "warnings": ["contrast 由 separation/2 归一化，属演示口径；真机以 iqraw 自带评估为准"],
    }


def _fit_piamp(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """幅度扫描（Rabi / π 脉冲幅度）：正弦拟合，π 幅度取拟合曲线第一个峰值位置。

    ``x`` 是扫描幅度、``y`` 是激发概率（或读出信号）。初值靠 FFT 给频率，
    否则 curve_fit 容易掉进「几乎不振荡」的坏盆里（真机数据尤其明显）。
    """
    span = float(x.max() - x.min()) or 1.0
    amp0 = float(np.max(y) - np.min(y)) / 2.0 or 1.0
    freq0 = 1.0 / max(span, 1e-9)
    try:
        centered = y - np.mean(y)
        spectrum = np.abs(np.fft.rfft(centered))
        freqs = np.fft.rfftfreq(y.size, d=span / max(y.size - 1, 1))
        if spectrum.size > 1:
            freq0 = float(freqs[int(np.argmax(spectrum[1:]) + 1)] or freq0)
    except Exception:
        pass
    p0 = (amp0, max(freq0, 1e-9), 0.0, float(np.mean(y)))
    bounds = ([0.0, 1e-6, -np.pi, -np.inf], [np.inf, np.inf, np.pi, np.inf])
    result = _curve_fit(_sinusoid, x, y, p0, bounds)
    if result is None:
        idx = int(np.argmax(y))
        return {
            "ok": True,
            "model": "argmax（正弦未收敛时的兜底）",
            "params": {"pi_amplitude": float(x[idx])},
            "metrics": {"pi_amplitude": round(float(x[idx]), 9)},
            "r2": 0.0,
            "warnings": ["正弦拟合未收敛，pi_amplitude 取自最大值对应幅度"],
        }
    popt, y_fit = result
    amp, freq, phase, offset = (float(v) for v in popt)
    period = 1.0 / max(abs(freq), 1e-12)
    # π 幅度 = 拟合曲线在第一个周期内的极大值位置（比解析反解稳）
    dense = np.linspace(float(x.min()), float(x.min()) + period, 2000)
    curve = _sinusoid(dense, amp, freq, phase, offset)
    peak = float(dense[int(np.argmax(curve))])
    return {
        "ok": True,
        "model": "A·sin(2πft+φ)+c",
        "params": {"amplitude": amp, "freq": freq, "phase": phase, "offset": offset, "period": period},
        "metrics": {"pi_amplitude": round(peak, 9)},
        "r2": _r2(y, y_fit),
    }


def _fit_drag(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    p0 = (1.0, 0.0, float(np.mean(y)))
    bounds = ([-np.inf, -np.inf, -np.inf], [np.inf, np.inf, np.inf])
    result = _curve_fit(_quadratic, x, y, p0, bounds)
    if result is None:
        idx = int(np.argmin(y))
        return {
            "ok": True,
            "model": "argmin（抛物线未收敛时的兜底）",
            "params": {"drag_delta": float(x[idx])},
            "metrics": {"drag_delta": round(float(x[idx]), 9)},
            "r2": 0.0,
            "warnings": ["抛物线拟合未收敛，drag_delta 取自极小值位置"],
        }
    popt, y_fit = result
    a, b, c = (float(v) for v in popt)
    vertex = -b / (2 * a) if abs(a) > 1e-15 else float(x[int(np.argmin(y))])
    return {
        "ok": True,
        "model": "a·x²+bx+c",
        "params": {"a": a, "b": b, "c": c},
        "metrics": {"drag_delta": round(vertex, 9)},
        "r2": _r2(y, y_fit),
    }


#: kind → 拟合器。别名在这里归一（ramsey 族共用一套）。
FITTERS: Dict[str, Callable[[np.ndarray, np.ndarray], Dict[str, Any]]] = {
    "t1": _fit_t1,
    "ramsey": _fit_ramsey,
    "ramsey_df": _fit_ramsey,
    "ramsey_t2": _fit_ramsey,
    "t2_echo": _fit_t2_echo,
    "spinecho_t2": _fit_t2_echo,
    "spectroscopy": _fit_spectroscopy,
    "pulsed_spec": _fit_spectroscopy,
    "spectroscopy_auto": _fit_spectroscopy,
    "s21": _fit_spectroscopy,
    "s21_dis": _fit_spectroscopy,
    "xeb": _fit_xeb,
    "iqraw": _fit_iqraw,
    "single_shot": _fit_iqraw,
    "piamp": _fit_piamp,
    "time_rabi": _fit_piamp,
    "rabi": _fit_piamp,
    "drag_calibrate": _fit_drag,
}

#: 别名 → 规范 kind
KIND_ALIASES: Dict[str, str] = {
    "t1_fixed_zpa": "t1",
    "ramsey_fringe": "ramsey",
    "spec": "spectroscopy",
    "iq": "iqraw",
    "allxy_drag": "drag_calibrate",
}


def normalize_kind(kind: Any) -> str:
    key = str(kind or "").strip().lower()
    key = KIND_ALIASES.get(key, key)
    return key


def kinds() -> List[str]:
    return sorted(FITTERS)


def analyse(kind: Any, data: Any, *, engine: str = "scipy") -> Dict[str, Any]:
    """对一份数据集做拟合。

    ``engine``：``scipy``（默认）/ ``lqcs``（需要厂商模块，失败会回落 scipy 并在
    ``engine_attempts`` 里留痕）/ ``auto``。
    """
    name = normalize_kind(kind)
    if name not in FITTERS:
        return {
            "ok": False,
            "kind": name or None,
            "reason": f"不支持的 kind：{kind!r}；可用：{', '.join(kinds())}",
            "kinds": kinds(),
        }
    try:
        x_raw, y_raw = extract_series(data)
    except Exception as exc:
        return {"ok": False, "kind": name, "reason": f"数据集无法解析为 (x, y)：{exc}"}
    x, y = _mask(x_raw, y_raw)
    if x.size < 4:
        return {"ok": False, "kind": name, "reason": f"有效数据点太少（{x.size} 个）"}

    attempts: List[Dict[str, Any]] = []
    chosen = str(engine or "scipy").strip().lower()
    if chosen in ("auto", "lqcs"):
        vendor = _lqcs_fit(name, x, y)
        attempts.append({"engine": "lqcs", "ok": bool(vendor.get("ok")), "reason": vendor.get("reason")})
        if vendor.get("ok"):
            vendor.update({"kind": name, "engine": "lqcs", "engine_attempts": attempts, "points": int(x.size)})
            return _annotate(vendor, y)
        if chosen == "lqcs":
            return {
                "ok": False,
                "kind": name,
                "reason": vendor.get("reason") or "lqcs 引擎拟合失败",
                "engine_attempts": attempts,
            }

    result = FITTERS[name](x, y)
    result.setdefault("ok", False)
    result.update({"kind": name, "engine": "scipy", "engine_attempts": attempts, "points": int(x.size)})
    return _annotate(result, y)


def _annotate(result: Dict[str, Any], y: np.ndarray) -> Dict[str, Any]:
    """统一补 ``r2`` 的 warnings 与 ``metrics`` 里的数值规范化。"""
    if not result.get("ok"):
        return result
    warnings = list(result.get("warnings") or [])
    r2 = result.get("r2")
    if isinstance(r2, (int, float)) and r2 < R2_WARN_THRESHOLD:
        warnings.append(f"拟合质量偏低（r²={float(r2):.3f}），结果仅供参考")
    if warnings:
        result["warnings"] = warnings
    metrics = result.get("metrics")
    if isinstance(metrics, dict):
        result["metrics"] = {
            k: (round(float(v), 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
            for k, v in metrics.items()
        }
    return result


def _lqcs_fit(kind: str, x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """厂商引擎：只有装了 LQCS 才有。这里只做「有没有」的判断并如实报告。

    真正的 ``qter.fitData`` 需要 DataVault 会话与活数据上下文，硬套到任意 (x, y)
    上会得到与数据集无关的结果 —— 所以本函数**不伪造**厂商拟合，
    仅在厂商模块存在时把它标记为「可用但需要 DataVault 上下文」。
    """
    try:
        import lqms  # noqa: F401  仅用于探测
    except Exception as exc:
        return {"ok": False, "reason": f"厂商引擎不可用：{exc.__class__.__name__}"}
    return {
        "ok": False,
        "reason": "厂商引擎需要 DataVault 会话上下文，离线 (x, y) 数据请用 scipy 引擎",
    }

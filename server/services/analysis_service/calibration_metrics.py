# -*- coding: utf-8 -*-
"""单比特标定的指标口径与门限（服务端唯一实现）。

这里只做**纯计算**：给定实验列 → 给指标；给定指标 → 给门限结论 / 下一步策略。
不连设备、不下发实验、不写寄存器。

门限不写在本模块里，唯一真源是 ``server/config/lab_criteria.json`` 的
``calibration_gates.thresholds``（见 :func:`gate_thresholds`）。
原实现曾在 ``tools/single_qubit_auto_calibration/``（已出库到本机历史归档），
口径搬到这里后由分析服务统一提供，工具层只做调用。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np

from ..common.metrics import load_criteria

# 仅作为 lab_criteria.json 缺字段时的兜底；正常路径一律读配置
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "fast_visibility_min": 0.05,
    "iq_snr_min": 0.1,
    "iq_norm_min": 0.1,
    "min_readout_fidelity": 0.8,
    "rabi_swing_min": 0.15,
    "rabi_sigma_k": 4.0,
    "max_detune_mhz": 0.1,
    "min_t1_us": 20.0,
}


def gate_thresholds(criteria: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """取标定门限。配置缺失时回落到 :data:`DEFAULT_THRESHOLDS`。"""
    crit = criteria if criteria is not None else load_criteria()
    raw = ((crit or {}).get("calibration_gates") or {}).get("thresholds") or {}
    out = dict(DEFAULT_THRESHOLDS)
    for key, value in raw.items():
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out


# ── 指标 ────────────────────────────────────────────────────────────────────
def iq_metrics(cols: Dict[str, Any]) -> Dict[str, Any]:
    """读出 IQ 聚类指标：分离度 / SNR / 归一化 / 保真度。"""
    need = ("Is | I", "Qs | I", "Is | X", "Qs | X")
    if any(k not in cols for k in need):
        return {"valid": False, "reason": "missing_iq_columns"}
    z0 = np.asarray(cols["Is | I"], float) + 1j * np.asarray(cols["Qs | I"], float)
    z1 = np.asarray(cols["Is | X"], float) + 1j * np.asarray(cols["Qs | X"], float)
    if len(z0) < 2 or len(z1) < 2:
        return {"valid": False, "reason": "too_few_iq_samples"}
    m0, m1 = z0.mean(), z1.mean()
    sep = float(abs(m1 - m0))
    s0 = float(np.sqrt(np.mean(abs(z0 - m0) ** 2)))
    s1 = float(np.sqrt(np.mean(abs(z1 - m1) ** 2)))
    snr = sep / np.sqrt((s0 * s0 + s1 * s1) / 2) if s0 + s1 else 0.0
    norm = sep / (abs(m0) + abs(m1)) if abs(m0) + abs(m1) else 0.0
    if sep:
        u = (m1 - m0) / sep
        p0 = np.real(z0 * np.conj(u))
        p1 = np.real(z1 * np.conj(u))
        sd = np.sqrt((p0.var() + p1.var()) / 2)
        fidelity = 0.5 * (1 + math.erf(abs(p1.mean() - p0.mean()) / (2 * sd))) if sd else 0.5
    else:
        fidelity = 0.5
    visibility = float(norm)
    return {
        "valid": True,
        "sep": sep,
        "snr": float(snr),
        "norm": float(norm),
        "visibility": visibility,
        "fidelity": float(fidelity),
        "maximum_visibility": visibility,
    }


def rabi_metrics(
    cols: Dict[str, Any],
    swing_min: Optional[float] = None,
    sigma_k: Optional[float] = None,
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Rabi 振荡指标：摆幅、噪声 sigma、最优点 amplitude。"""
    th = thresholds if thresholds is not None else gate_thresholds()
    if swing_min is None:
        swing_min = th["rabi_swing_min"]
    if sigma_k is None:
        sigma_k = th["rabi_sigma_k"]
    if "piAmp" not in cols or "P1" not in cols:
        return {"valid": False, "ok": False, "reason": "missing_rabi_columns"}
    x = np.asarray(cols["piAmp"], float)
    y = np.asarray(cols["P1"], float)
    if len(x) < 6:
        return {"valid": False, "ok": False, "reason": "too_few_rabi_samples"}
    d3 = y[:-3] - 3 * y[1:-2] + 3 * y[2:-1] - y[3:]
    sigma = 1.4826 * float(np.median(abs(d3 - np.median(d3)))) / np.sqrt(20.0)
    best = None
    for i in range(1, len(y) - 1):
        if not ((y[i] > y[i - 1] and y[i] >= y[i + 1]) or (y[i] >= y[i - 1] and y[i] > y[i + 1])):
            continue
        j = i + 1 + int(np.argmin(y[i + 1:]))
        if j >= len(y) - 1:
            continue
        swing = float(y[i] - y[j])
        score = swing / sigma if sigma > 0 else (float("inf") if swing > 0 else 0.0)
        if swing >= swing_min and score >= sigma_k and (best is None or score > best["score"]):
            best = {"peak_idx": i, "trough_idx": j, "swing": swing, "score": score}
    out = {
        "valid": True,
        "ok": best is not None,
        "sigma": sigma,
        "p1_min": float(y.min()),
        "p1_max": float(y.max()),
        "amp_range": [float(x.min()), float(x.max())],
    }
    if best:
        out.update(best)
        out["amp_opt"] = float(x[best["peak_idx"]])
    return out


def spectroscopy_metrics(cols: Dict[str, Any]) -> Dict[str, Any]:
    """频率扫描：取 P1 峰位置作为 f10。"""
    if "freq" not in cols or "P1" not in cols:
        return {"valid": False, "reason": "missing_spectroscopy_columns"}
    x, y = np.asarray(cols["freq"], float), np.asarray(cols["P1"], float)
    if len(x) < 3:
        return {"valid": False, "reason": "too_few_spectroscopy_samples"}
    i = int(np.argmax(y))
    return {"valid": True, "f10": float(x[i]), "p1_peak": float(y[i]), "p1_base": float(np.median(y))}


def s21_metrics(cols: Dict[str, Any]) -> Dict[str, Any]:
    """S21 反射读数：找 0/1 幅度极小点与 0-1 距离最大点。"""
    if "freq" not in cols:
        return {"valid": False, "reason": "missing_frequency"}
    x = np.asarray(cols["freq"], float)
    out: Dict[str, Any] = {"valid": True}
    for key, tag in (("Amplitude | 0", "amp0"), ("Amplitude | 1", "amp1")):
        if key in cols:
            y = np.asarray(cols[key], float)
            out["fread_%s" % tag] = float(x[int(np.argmin(y))])
    if "distance | 01" in cols:
        d = np.asarray(cols["distance | 01"], float)
        out["distance01_max"] = float(d.max())
        out["fread_best"] = float(x[int(np.argmax(d))])
    return out


def ramsey_metrics(cols: Dict[str, Any]) -> Dict[str, Any]:
    """Ramsey 结果：优先用已有拟合列，否则说明需要外部拟合。"""
    if "detune_MHz" in cols:
        detune = float(np.asarray(cols["detune_MHz"], float).ravel()[0])
        return {
            "valid": True,
            "detune_mhz": detune,
            "T2star_us": float(np.asarray(cols.get("T2star_us", [0]), float).ravel()[0]),
        }
    if "delay" not in cols or "P1" not in cols:
        return {"valid": False, "reason": "missing_ramsey_columns"}
    return {"valid": False, "reason": "ramsey_fit_requires_external_fit"}


# ── 门限与策略 ──────────────────────────────────────────────────────────────
def iq_fast_pass(iq: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None) -> bool:
    """快速门限：只要可见度够就先往下走。"""
    th = thresholds if thresholds is not None else gate_thresholds()
    return bool(iq.get("valid") and iq.get("maximum_visibility", 0.0) > th["fast_visibility_min"])


def iq_final_pass(iq: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None) -> bool:
    """终判门限：SNR、归一化、读出保真度同时达标。"""
    th = thresholds if thresholds is not None else gate_thresholds()
    return bool(
        iq.get("valid")
        and iq.get("snr", 0.0) > th["iq_snr_min"]
        and iq.get("norm", 0.0) > th["iq_norm_min"]
        and iq.get("fidelity", 0.0) >= th["min_readout_fidelity"]
    )


def rabi_pass(rabi: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None) -> bool:
    """Rabi 门限：摆幅与信噪倍数同时达标。"""
    th = thresholds if thresholds is not None else gate_thresholds()
    return bool(
        rabi.get("ok")
        and rabi.get("swing", 0.0) >= th["rabi_swing_min"]
        and rabi.get("score", 0.0) >= th["rabi_sigma_k"]
    )


def final_pass(
    iq: Dict[str, Any],
    rabi: Dict[str, Any],
    ramsey: Optional[Dict[str, Any]],
    t1_us: Optional[float],
    thresholds: Optional[Dict[str, float]] = None,
) -> bool:
    """单比特是否算标定完成：读出 + Rabi + 失谐 + T1 全过。"""
    th = thresholds if thresholds is not None else gate_thresholds()
    detune_ok = ramsey is None or abs(float(ramsey.get("detune_mhz", 0.0))) < th["max_detune_mhz"]
    t1_ok = t1_us is None or float(t1_us) >= th["min_t1_us"]
    return iq_final_pass(iq, th) and rabi_pass(rabi, th) and detune_ok and t1_ok


def choose_strategy(
    *,
    s21_ok: bool,
    zpa_ok: bool,
    iq: Dict[str, Any],
    rabi: Dict[str, Any],
    ramsey: Optional[Dict[str, Any]] = None,
    t1_us: Optional[float] = None,
    readout_distance: Optional[float] = None,
    thresholds: Optional[Dict[str, float]] = None,
) -> str:
    """给定当前证据，给下一步该做什么（只给建议，不执行）。"""
    th = thresholds if thresholds is not None else gate_thresholds()
    if not s21_ok:
        return "RETRY_S21_OR_TRY_SECOND_CAVITY"
    if not zpa_ok:
        return "EXPAND_OR_REFINE_ZPA_RANGE"
    iq_ok = iq_fast_pass(iq, th)
    rabi_ok = rabi_pass(rabi, th)
    if not iq_ok and rabi_ok:
        return "OPTIMIZE_READOUT"
    if iq_ok and not rabi_ok:
        return "REOPTIMIZE_F10_AND_PIAMP"
    if not iq_ok and not rabi_ok:
        return "CHECK_FREAD_ZPA_AND_F10"
    if ramsey and abs(float(ramsey.get("detune_mhz", 0.0))) >= th["max_detune_mhz"]:
        return "RUN_PIDF_AND_RAMSEY"
    if t1_us is not None and t1_us < th["min_t1_us"]:
        return "SCAN_ZPA_FOR_T1"
    if readout_distance is not None and readout_distance < th["fast_visibility_min"]:
        return "OPTIMIZE_READOUT_AND_DISCRIMINATOR"
    return "FINAL_VERIFY"

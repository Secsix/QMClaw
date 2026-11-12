from __future__ import annotations

import math
from typing import Any

import numpy as np


def iq_metrics(cols: dict[str, Any]) -> dict[str, Any]:
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
    return {"valid": True, "sep": sep, "snr": float(snr), "norm": float(norm),
            "visibility": visibility, "fidelity": float(fidelity),
            "maximum_visibility": visibility}


def rabi_metrics(cols: dict[str, Any], swing_min: float = 0.15, sigma_k: float = 4.0) -> dict[str, Any]:
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
        score = swing / sigma if sigma > 0 else float("inf") if swing > 0 else 0.0
        if swing >= swing_min and score >= sigma_k and (best is None or score > best["score"]):
            best = {"peak_idx": i, "trough_idx": j, "swing": swing, "score": score}
    out = {"valid": True, "ok": best is not None, "sigma": sigma,
           "p1_min": float(y.min()), "p1_max": float(y.max()), "amp_range": [float(x.min()), float(x.max())]}
    if best:
        out.update(best)
        out["amp_opt"] = float(x[best["peak_idx"]])
    return out


def spectroscopy_metrics(cols: dict[str, Any]) -> dict[str, Any]:
    if "freq" not in cols or "P1" not in cols:
        return {"valid": False, "reason": "missing_spectroscopy_columns"}
    x, y = np.asarray(cols["freq"], float), np.asarray(cols["P1"], float)
    if len(x) < 3:
        return {"valid": False, "reason": "too_few_spectroscopy_samples"}
    i = int(np.argmax(y))
    return {"valid": True, "f10": float(x[i]), "p1_peak": float(y[i]), "p1_base": float(np.median(y))}


def s21_metrics(cols: dict[str, Any]) -> dict[str, Any]:
    if "freq" not in cols:
        return {"valid": False, "reason": "missing_frequency"}
    x = np.asarray(cols["freq"], float)
    out = {"valid": True}
    for key, tag in (("Amplitude | 0", "amp0"), ("Amplitude | 1", "amp1")):
        if key in cols:
            y = np.asarray(cols[key], float)
            out[f"fread_{tag}"] = float(x[int(np.argmin(y))])
    if "distance | 01" in cols:
        d = np.asarray(cols["distance | 01"], float)
        out["distance01_max"] = float(d.max())
        out["fread_best"] = float(x[int(np.argmax(d))])
    return out


def ramsey_metrics(cols: dict[str, Any]) -> dict[str, Any]:
    if "detune_MHz" in cols:
        detune = float(np.asarray(cols["detune_MHz"], float).ravel()[0])
        return {"valid": True, "detune_mhz": detune, "T2star_us": float(np.asarray(cols.get("T2star_us", [0]), float).ravel()[0])}
    if "delay" not in cols or "P1" not in cols:
        return {"valid": False, "reason": "missing_ramsey_columns"}
    return {"valid": False, "reason": "ramsey_fit_requires_external_fit"}

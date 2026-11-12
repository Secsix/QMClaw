from __future__ import annotations

import numpy as np

from .runner import run_experiment


def local_s21(adapter, qubit, fread_ref: float, half_span: float, points: int):
    freq = np.linspace(fread_ref - half_span, fread_ref + half_span, points)
    return run_experiment(adapter, "s21", qubit, freq=freq.tolist(), update=False, do_plot=False)


def choose_candidates(result, max_candidates: int = 2) -> list[float]:
    m = result.metrics
    candidates = [m[k] for k in ("fread_amp0", "fread_amp1") if m.get(k) is not None]
    if m.get("fread_best") is not None:
        candidates.append(m["fread_best"])
    return list(dict.fromkeys(round(float(x), 12) for x in candidates))[:max_candidates]

from __future__ import annotations

import numpy as np

from .runner import run_experiment


def scan(adapter, qubit, fread: float, zpa_min: float, zpa_max: float, step: float):
    points = np.arange(zpa_min, zpa_max + step * 0.5, step)
    results = []
    for zpa in points:
        res = run_experiment(adapter, "spectroscopy", qubit, zpa=float(zpa), fread=fread,
                             update=False, do_plot=False)
        results.append((float(zpa), res))
    return results


def infer_range(results, min_peak: float = 0.05):
    good = []
    for zpa, result in results:
        peak = result.metrics.get("p1_peak", 0.0)
        if result.ok and peak >= min_peak:
            good.append(zpa)
    if not good:
        return None
    return min(good), max(good)

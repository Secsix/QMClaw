from __future__ import annotations

from typing import Any

from ..models import ExperimentResult
from ..analysis.metrics import iq_metrics, rabi_metrics, ramsey_metrics, s21_metrics, spectroscopy_metrics


METRIC_FUNCTIONS = {
    "iqraw": iq_metrics,
    "piamp": rabi_metrics,
    "ramsey_df": ramsey_metrics,
    "s21": s21_metrics,
    "s21_dis": s21_metrics,
    "spectroscopy": spectroscopy_metrics,
}


def run_experiment(adapter: Any, name: str, qubit: Any = None, **params: Any) -> ExperimentResult:
    try:
        raw = adapter.run(name, qubit, **params)
    except Exception as exc:  # adapter errors become auditable results
        return ExperimentResult(name, params=params, ok=False, error=str(exc))
    if isinstance(raw, ExperimentResult):
        return raw
    if raw is None:
        return ExperimentResult(name, params=params, ok=False, error="empty_result")
    if isinstance(raw, dict) and "columns" in raw:
        columns = raw.get("columns", {})
        dataset = raw.get("dataset")
        direct_metrics = raw.get("metrics", {})
    elif isinstance(raw, dict):
        columns, dataset, direct_metrics = raw, None, {}
    else:
        return ExperimentResult(name, params=params, ok=True, metrics={"value": raw})
    fn = METRIC_FUNCTIONS.get(name)
    metrics = fn(columns) if fn else {}
    if direct_metrics:
        metrics.update(direct_metrics)
    # Simple result dictionaries such as {T1_us: 30} are already metrics.
    if name not in METRIC_FUNCTIONS and columns:
        metrics = dict(columns)
    return ExperimentResult(name, params=params, columns=columns, metrics=metrics,
                            ok=bool(metrics.get("valid", True)), dataset=dataset)

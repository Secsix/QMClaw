from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import CalibrationConfig, CandidateWorkingPoint
from .quality_gate import choose_strategy


@dataclass
class StrategyDecision:
    action: str
    reason: str
    experiments: list[str]
    params: dict[str, Any]


def decide(result: dict[str, Any], cfg: CalibrationConfig) -> StrategyDecision:
    action = choose_strategy(s21_ok=bool(result.get("s21_ok")), zpa_ok=bool(result.get("zpa_ok")),
                            iq=result.get("iq", {}), rabi=result.get("rabi", {}),
                            ramsey=result.get("ramsey"), t1_us=result.get("t1_us"),
                            readout_distance=result.get("readout_distance"), cfg=cfg)
    plans = {
        "RETRY_S21_OR_TRY_SECOND_CAVITY": (["s21"], {"try_alternate_candidate": True}),
        "EXPAND_OR_REFINE_ZPA_RANGE": (["s21_zpa"], {"expand": True, "refine": True}),
        "OPTIMIZE_READOUT": (["s21_dis", "iqraw"], {"update": False}),
        "REOPTIMIZE_F10_AND_PIAMP": (["spectroscopy", "piamp"], {"update": False}),
        "CHECK_FREAD_ZPA_AND_F10": (["s21", "spectroscopy", "iqraw", "piamp"], {"update": False}),
        "RUN_PIDF_AND_RAMSEY": (["pidf", "ramsey_df"], {"df": 0.08, "update": False}),
        "SCAN_ZPA_FOR_T1": (["t1", "iqraw", "piamp"], {"local_scan": True}),
        "OPTIMIZE_READOUT_AND_DISCRIMINATOR": (["s21_dis", "iqraw"], {"update": False}),
        "FINAL_VERIFY": (["iqraw", "piamp", "ramsey_df", "t1"], {"update": False}),
    }
    experiments, params = plans[action]
    return StrategyDecision(action, action.replace("_", " ").lower(), experiments, params)


def score_candidate(metrics: dict[str, Any], cfg: CalibrationConfig) -> float:
    iq = metrics.get("iq", {})
    rabi = metrics.get("rabi", {})
    return (min(float(iq.get("snr", 0.0)), 10.0) / 10.0
            + min(float(iq.get("fidelity", 0.0)), 1.0)
            + min(float(rabi.get("swing", 0.0)), 1.0)
            - abs(float(metrics.get("detune_mhz", 0.0))) / max(cfg.max_detune_mhz, 1e-9) * 0.1)

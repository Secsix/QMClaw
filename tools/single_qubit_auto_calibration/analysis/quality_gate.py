from __future__ import annotations

from typing import Any

from ..models import CalibrationConfig


def iq_fast_pass(iq: dict[str, Any], cfg: CalibrationConfig) -> bool:
    return bool(iq.get("valid") and iq.get("maximum_visibility", 0.0) > cfg.fast_visibility_min)


def iq_final_pass(iq: dict[str, Any], cfg: CalibrationConfig) -> bool:
    return bool(iq.get("valid") and iq.get("snr", 0.0) > cfg.iq_snr_min
                and iq.get("norm", 0.0) > cfg.iq_norm_min
                and iq.get("fidelity", 0.0) >= cfg.min_readout_fidelity)


def rabi_pass(rabi: dict[str, Any], cfg: CalibrationConfig) -> bool:
    return bool(rabi.get("ok") and rabi.get("swing", 0.0) >= cfg.rabi_swing_min
                and rabi.get("score", 0.0) >= cfg.rabi_sigma_k)


def final_pass(iq: dict[str, Any], rabi: dict[str, Any], ramsey: dict[str, Any] | None,
               t1_us: float | None, cfg: CalibrationConfig) -> bool:
    detune_ok = ramsey is None or abs(float(ramsey.get("detune_mhz", 0.0))) < cfg.max_detune_mhz
    t1_ok = t1_us is None or float(t1_us) >= cfg.min_t1_us
    return iq_final_pass(iq, cfg) and rabi_pass(rabi, cfg) and detune_ok and t1_ok


def choose_strategy(*, s21_ok: bool, zpa_ok: bool, iq: dict[str, Any], rabi: dict[str, Any],
                    ramsey: dict[str, Any] | None = None, t1_us: float | None = None,
                    readout_distance: float | None = None, cfg: CalibrationConfig) -> str:
    if not s21_ok:
        return "RETRY_S21_OR_TRY_SECOND_CAVITY"
    if not zpa_ok:
        return "EXPAND_OR_REFINE_ZPA_RANGE"
    iq_ok = iq_fast_pass(iq, cfg)
    rabi_ok = rabi_pass(rabi, cfg)
    if not iq_ok and rabi_ok:
        return "OPTIMIZE_READOUT"
    if iq_ok and not rabi_ok:
        return "REOPTIMIZE_F10_AND_PIAMP"
    if not iq_ok and not rabi_ok:
        return "CHECK_FREAD_ZPA_AND_F10"
    if ramsey and abs(float(ramsey.get("detune_mhz", 0.0))) >= cfg.max_detune_mhz:
        return "RUN_PIDF_AND_RAMSEY"
    if t1_us is not None and t1_us < cfg.min_t1_us:
        return "SCAN_ZPA_FOR_T1"
    if readout_distance is not None and readout_distance < cfg.fast_visibility_min:
        return "OPTIMIZE_READOUT_AND_DISCRIMINATOR"
    return "FINAL_VERIFY"

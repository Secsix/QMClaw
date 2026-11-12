from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class CalibrationState(str, Enum):
    INIT = "INIT"
    INPUT_VALIDATED = "INPUT_VALIDATED"
    CAVITY_CANDIDATES_FOUND = "CAVITY_CANDIDATES_FOUND"
    ZPA_RANGE_FOUND = "ZPA_RANGE_FOUND"
    SEARCHING_ACTIVE_POINT = "SEARCHING_ACTIVE_POINT"
    ACTIVE_POINT_FOUND = "ACTIVE_POINT_FOUND"
    F10_OPTIMIZED = "F10_OPTIMIZED"
    READOUT_OPTIMIZED = "READOUT_OPTIMIZED"
    PI_OPTIMIZED = "PI_OPTIMIZED"
    FREQUENCY_TUNED = "FREQUENCY_TUNED"
    RELAXATION_VERIFIED = "RELAXATION_VERIFIED"
    FINAL_VERIFIED = "FINAL_VERIFIED"
    COMPLETE = "COMPLETE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


@dataclass
class QubitInput:
    qubit_id: str
    fread_ref: float | None = None
    zpa_min: float | None = None
    zpa_max: float | None = None
    source: str = "manual"
    confidence: float = 1.0


@dataclass
class ExperimentResult:
    experiment: str
    params: dict[str, Any] = field(default_factory=dict)
    columns: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    dataset: str | None = None


@dataclass
class CandidateWorkingPoint:
    qubit_id: str
    zpa: float
    fread: float | None = None
    f10: float | None = None
    f21: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    source: str = "scan"


@dataclass
class CalibrationConfig:
    s21_half_span: float = 0.020
    s21_expanded_spans: tuple[float, ...] = (0.050, 0.100)
    s21_points: int = 41
    zpa_step: float = 0.2
    zpa_refine_step: float = 0.05
    zpa_expand_step: float = 0.5
    fast_visibility_min: float = 0.05
    iq_snr_min: float = 0.1
    iq_norm_min: float = 0.1
    rabi_swing_min: float = 0.15
    rabi_sigma_k: float = 4.0
    max_detune_mhz: float = 0.1
    min_t1_us: float = 20.0
    min_readout_fidelity: float = 0.8
    piamp_span: float = 3.0
    max_retries: int = 2
    early_stop: bool = True
    dry_run: bool = True
    allow_execute: bool = False
    allow_set_params: bool = False
    register_limits: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "fread": (0.1, 100.0), "f10": (0.1, 20.0), "f21": (0.1, 20.0),
        "bias_z": (-20.0, 20.0), "PiGate.amp": (0.0, 20.0),
        "PiHalf.amp": (0.0, 20.0),
    })


@dataclass
class CalibrationRun:
    input: QubitInput
    config: CalibrationConfig
    state: CalibrationState = CalibrationState.INIT
    results: list[ExperimentResult] = field(default_factory=list)
    candidates: list[CandidateWorkingPoint] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    final: CandidateWorkingPoint | None = None
    failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        d["config"]["s21_expanded_spans"] = list(self.config.s21_expanded_spans)
        return d

from __future__ import annotations

from typing import Any

from .models import CalibrationConfig


class SafetyError(RuntimeError):
    pass


def validate_register(name: str, value: float, config: CalibrationConfig) -> None:
    if name in config.register_limits:
        lo, hi = config.register_limits[name]
        if not lo <= value <= hi:
            raise SafetyError(f"{name}={value} outside [{lo}, {hi}]")


def require_execution(config: CalibrationConfig) -> None:
    if not config.allow_execute:
        raise SafetyError("real experiment execution is disabled; set allow_execute explicitly")


def require_parameter_write(config: CalibrationConfig) -> None:
    if not config.allow_set_params:
        raise SafetyError("parameter writes are disabled; set allow_set_params explicitly")


def build_assignment_plan(qubit_id: str, params: dict[str, Any], config: CalibrationConfig) -> dict[str, Any]:
    checked = {}
    for name, value in params.items():
        if value is None:
            continue
        validate_register(name, float(value), config)
        checked[name] = float(value)
    return {"qubit": qubit_id, "params": checked, "dry_run": True, "requires_approval": True}

from __future__ import annotations

from typing import Any

from .base import AdapterError
from ..safety import require_execution, require_parameter_write, validate_register
from ..models import CalibrationConfig


class LabRADAdapter:
    """LabRAD bridge placeholder with explicit execution/write gates.

    The adapter does not import or connect LabRAD until a caller injects a
    connection and explicitly enables the corresponding gate.
    """
    def __init__(self, sq_module: Any, config: CalibrationConfig):
        self.sq = sq_module
        self.config = config

    def run(self, name: str, qubit: Any, **params: Any) -> Any:
        require_execution(self.config)
        func = getattr(self.sq, name, None)
        if not callable(func):
            raise AdapterError(f"sq.{name} is unavailable")
        return func(qubit, **params)

    def set_params(self, qubit: Any, params: dict[str, Any]) -> dict[str, Any]:
        require_parameter_write(self.config)
        for name, value in params.items():
            validate_register(name, float(value), self.config)
            target = qubit.regs
            bits = name.split(".")
            for bit in bits[:-1]:
                target = getattr(target, bit)
            setattr(target, bits[-1], value)
        return params

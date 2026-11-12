from __future__ import annotations

from typing import Any, Protocol


class ExperimentAdapter(Protocol):
    def run(self, name: str, qubit: Any, **params: Any) -> Any: ...


class AdapterError(RuntimeError):
    pass

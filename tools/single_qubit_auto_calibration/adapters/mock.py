from __future__ import annotations

from typing import Any


class MockAdapter:
    def __init__(self, datasets: dict[str, Any] | None = None):
        self.datasets = datasets or {}
        self.calls: list[dict[str, Any]] = []

    def run(self, name: str, qubit: Any = None, **params: Any) -> Any:
        self.calls.append({"name": name, "qubit": qubit, "params": params})
        value = self.datasets.get(name)
        if callable(value):
            return value(qubit, **params)
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

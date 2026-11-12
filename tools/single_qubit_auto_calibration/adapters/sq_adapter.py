from __future__ import annotations

import inspect
from typing import Any

from .base import AdapterError


class SqAdapter:
    """统一 sq.* 调用；默认不连接 LabRAD，需显式注入 sq module。"""

    def __init__(self, sq_module: Any, *, execute: bool = False):
        self.sq = sq_module
        self.execute = execute

    def run(self, name: str, qubit: Any, **params: Any) -> Any:
        if not self.execute:
            raise AdapterError("SqAdapter is read-only; enable execute explicitly")
        func = getattr(self.sq, name, None)
        if not callable(func):
            raise AdapterError(f"sq.{name} is unavailable")
        return func(qubit, **params)

    def signature(self, name: str) -> str | None:
        func = getattr(self.sq, name, None)
        if not callable(func):
            return None
        try:
            return str(inspect.signature(func))
        except (TypeError, ValueError):
            return "(...)"

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from .models import CalibrationConfig


def load_config(path: str | Path | None = None, **overrides: Any) -> CalibrationConfig:
    data: dict[str, Any] = {}
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.update({k: v for k, v in overrides.items() if v is not None})
    valid = {f.name for f in fields(CalibrationConfig)}
    data = {k: v for k, v in data.items() if k in valid}
    if "s21_expanded_spans" in data:
        data["s21_expanded_spans"] = tuple(data["s21_expanded_spans"])
    return replace(CalibrationConfig(), **data)


def save_config(config: CalibrationConfig, path: str | Path) -> None:
    data = dict(config.__dict__)
    data["s21_expanded_spans"] = list(config.s21_expanded_spans)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

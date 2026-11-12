from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_run(run: Any, path: str | Path) -> None:
    Path(path).write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

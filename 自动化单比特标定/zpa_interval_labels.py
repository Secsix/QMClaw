"""zpa_interval_labels.py — 人工标注的 ZPA 有效区间（训练集）。

数据来自现场人工看图判断（2026-11-13，session 20261113_20qubits）。
原始标注存在 ``zpa_interval_labels.json``；这里给出读取接口。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
LABEL_FILE = HERE / "zpa_interval_labels.json"

LABELS: List[dict] = json.loads(LABEL_FILE.read_text(encoding="utf-8"))["labels"]


def load_labeled_files(directory) -> List[Tuple[str, Path, Tuple[float, float]]]:
    """Return [(name, path, (lo, hi))] for every label whose file is present."""
    out = []
    for item in LABELS:
        p = Path(directory) / item["file"]
        if p.exists():
            out.append((item["file"], p,
                        (float(item["valid_zpa_interval"][0]),
                         float(item["valid_zpa_interval"][1]))))
    return out

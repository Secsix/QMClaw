"""zpa_interval_labels.py — 人工标注的 ZPA 有效区间（训练集）。

数据来自现场人工看图判断（2026-11-13，session 20261113_20qubits）。
原始标注存在 ``zpa_interval_labels.json``；这里给出读取接口。

**该 json 是现场实测标注，不入库**（见仓库 .gitignore）：clone 下来的版本库里
没有它。本模块在文件缺失时降级为空标注集（``LABELS = []``），让 import 不会
炸；真正需要标注的训练/验收脚本会自己给出明确提示。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
LABEL_FILE = HERE / "zpa_interval_labels.json"


def _load_labels() -> List[dict]:
    """读标注文件；缺失 / 损坏时返回空列表（不抛异常）。"""
    try:
        raw = json.loads(LABEL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    labels = raw.get("labels") if isinstance(raw, dict) else None
    return list(labels) if isinstance(labels, list) else []


LABELS: List[dict] = _load_labels()


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

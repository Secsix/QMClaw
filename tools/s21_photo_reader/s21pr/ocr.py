"""OCR 封装：RapidOCR(onnxruntime) 封装，返回归一化坐标的文本框。

依赖装在仓库根的 .pylibs（见 README 的安装小节）；也可通过环境变量
S21PR_LIB_DIR 或 config 中的 lib_dir 指定。
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class TextBox:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    score: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def to_dict(self):
        d = asdict(self)
        d["cx"], d["cy"] = self.cx, self.cy
        return d


def _bootstrap_libs(lib_dir: str | None = None) -> None:
    """把 OCR 依赖目录插到 sys.path 最前面。"""
    cands = []
    if lib_dir:
        cands.append(Path(lib_dir))
    if os.environ.get("S21PR_LIB_DIR"):
        cands.append(Path(os.environ["S21PR_LIB_DIR"]))
    cands += [REPO_ROOT / ".pylibs", REPO_ROOT / "tools" / "s21_photo_reader" / ".libs"]
    for c in cands:
        if c and c.is_dir():
            s = str(c)
            if s not in sys.path:
                sys.path.insert(0, s)
            return
    raise RuntimeError(
        "找不到 OCR 依赖目录（.pylibs）。请按 README 安装，或设置环境变量 S21PR_LIB_DIR。"
    )


_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def run_ocr_image(img, scale: float = 2.0):
    """对 PIL 图片做 OCR，返回文本框列表（坐标已还原到原图尺度）。"""
    from PIL import Image

    tmp = img
    if scale and abs(scale - 1.0) > 1e-6:
        tmp = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)

    result, _ = _engine()(tmp)
    boxes: list[TextBox] = []
    for item in result or []:
        box, text, score = item
        xs = [p[0] / scale for p in box]
        ys = [p[1] / scale for p in box]
        boxes.append(TextBox(text.strip(), min(xs), min(ys), max(xs), max(ys), float(score)))
    return boxes


def run_ocr(image_path, scale: float = 2.0, lib_dir: str | None = None,
            dump: str | None = None) -> list[TextBox]:
    """对图片做 OCR，返回文本框列表（坐标已还原到原图尺度）。

    scale > 1 时先放大再识别，对小字号/细线条的图更稳。
    """
    _bootstrap_libs(lib_dir)
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    boxes = run_ocr_image(img, scale)

    if dump:
        Path(dump).parent.mkdir(parents=True, exist_ok=True)
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump([b.to_dict() for b in boxes], fh, ensure_ascii=False, indent=1)
    return boxes


NUM_RE = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")
LIST_RE = re.compile(r"[\[\(\{].*[\]\)\}]")
QUBIT_RE = re.compile(r"^[qQ]\s*-?\d+([_\-]\d+)*$")
NUMISH_RE = re.compile(r"^[^\d]*\d[\d.,]*$")


def is_number(text: str) -> bool:
    return bool(NUM_RE.match(text.strip().replace(" ", "")))


def is_numericish(text: str) -> bool:
    """OCR 常把数字认成 ".5560" / "6,6225" 这类"近似数字"，放宽后仍当作数值。"""
    t = text.strip().replace(" ", "").replace(",", ".")
    if not t or not NUMISH_RE.match(t):
        return False
    try:
        float(t.rstrip("."))
    except ValueError:
        return False
    return True


def parse_number(text: str):
    t = text.strip().replace(" ", "").replace(",", ".")
    try:
        return float(t.rstrip("."))
    except ValueError:
        return None


def is_qubit(text: str) -> bool:
    return bool(QUBIT_RE.match(text.strip()))

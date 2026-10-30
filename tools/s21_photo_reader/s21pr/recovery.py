"""局部放大二次识别：救回整图 OCR 漏掉/误识的比特标签。

整图 OCR 对小字号的比特名（q15_11 这类）容易漏检或读成乱码；
这里在每个波谷附近裁一块放大 4 倍再识别，通常能把标签救回来。
"""
from __future__ import annotations

from PIL import Image

from .ocr import TextBox, is_qubit, run_ocr_image


def recover_qubit_labels(image_path, dips, curve, axes, scale: float = 4.0,
                         left: int = 280, right: int = 40,
                         up: int = 100, down: int = 100,
                         known_texts: set | None = None,
                         known_boxes: list | None = None,
                         max_dx: float = 150.0):
    """返回 [(dip, TextBox)]：为还没有标签的波谷找回标签。"""
    from PIL import Image as _I
    known = set(known_texts or ())
    kboxes = list(known_boxes or ())
    img = _I.open(image_path).convert("RGB")
    found = []
    used = set(known)

    def overlaps_known(bx, x0, y0):
        for kb in kboxes:
            if not (kb.x0 - 6 <= bx.x0 + x0 <= kb.x1 + 6):
                continue
            if not (kb.y0 - 6 <= bx.y0 + y0 <= kb.y1 + 6):
                continue
            return True
        return False

    for d in dips:
        if d.qubit:
            continue
        x0 = int(max(0, d.anchor_x - left))
        x1 = int(min(img.width, d.anchor_x + right))
        y_ref = d.px_trough if d.px_trough is not None else d.annot_y1
        y0 = int(max(0, y_ref - up))
        y1 = int(min(img.height, y_ref + down))
        if x1 - x0 < 20 or y1 - y0 < 15:
            continue
        crop = img.crop((x0, y0, x1, y1))
        try:
            boxes = run_ocr_image(crop, scale=scale)
        except Exception:
            continue
        cands = []
        for bx in boxes:
            key = bx.text.strip()
            if not is_qubit(key) or key in used:
                continue
            if any(key in k or k in key for k in known):   # 与已知标签重复/截断
                continue
            gx = bx.cx + x0
            if abs(gx - d.anchor_x) > max_dx:               # 离波谷太远，不认
                continue
            if overlaps_known(bx, x0, y0):
                continue
            cands.append((abs(gx - d.anchor_x), key, bx, gx))
        if not cands:
            continue
        cands.sort()
        _, key, bx, gx = cands[0]
        used.add(key)
        nb = TextBox(key, bx.x0 + x0, bx.y0 + y0, bx.x1 + x0, bx.y1 + y0, bx.score)
        found.append((d, nb))
    return found

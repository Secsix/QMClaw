"""比特标签 ↔ 波谷标注 的配对。

图中比特名与它对应的数值标注可能左右错开（避让文字），
所以不能只按 x 排序硬配。用单调 DP 对齐：
  代价 = |Δx| + w * |Δy| ，其中 Δy 用标签中心与波谷点的纵向距离，
既保证顺序单调，又利用纵向对齐关系做校验。
"""
from __future__ import annotations

import numpy as np


def align(labels, annots, wx: float = 1.0, wy: float = 1.0,
          sx: float = 50.0, sy: float = 40.0):
    """labels/annots: 需含 .cx/.cy 属性；annots 需含 .px_trough 属性（波谷 y）。

    代价按尺度归一化（默认 50px 横向 / 40px 纵向记 1 个代价），
    这样不同尺寸、不同标注习惯的图都能用同一套权重。
    返回 [(label, annot, cost)]，按 x 顺序。
    """
    n, m = len(labels), len(annots)
    if n == 0 or m == 0:
        return []

    cost = np.zeros((n, m))
    for i, lb in enumerate(labels):
        for j, an in enumerate(annots):
            alt = getattr(an, "label_cx", None) or an.anchor_x
            dx = min(abs(lb.cx - an.anchor_x), abs(lb.cx - alt))
            tr = an.px_trough if an.px_trough is not None else an.annot_y1
            dy = abs(lb.cy - tr)
            cost[i, j] = wx * dx / sx + wy * dy / sy

    INF = 1e12
    dp = np.full((n + 1, m + 1), INF)
    dp[0, 0] = 0.0
    back = np.zeros((n + 1, m + 1), dtype=int)   # 1: 配对  2: 跳 label  3: 跳 annot
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            opts = [
                (dp[i - 1, j - 1] + cost[i - 1, j - 1], 1),
                (dp[i - 1, j] + 50.0, 2),
                (dp[i, j - 1] + 50.0, 3),
            ]
            best = min(opts)
            dp[i, j], back[i, j] = best

    pairs, i, j = [], n, m
    while i > 0 or j > 0:
        b = back[i, j]
        if b == 1 and i > 0 and j > 0:
            pairs.append((labels[i - 1], annots[j - 1], float(cost[i - 1, j - 1])))
            i -= 1
            j -= 1
        elif b == 2 and i > 0:
            i -= 1
        elif j > 0:
            j -= 1
        else:
            break
    pairs.reverse()
    return pairs

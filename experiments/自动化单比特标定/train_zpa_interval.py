"""train_zpa_interval.py — 用人工标注的 ZPA 有效区间**训练**zpa2d 区间识别器。

输入：``zpa_interval_labels.json``（现场人工标注 5 张）+ DataVault 里的历史 S21ZPA 图。
输出：``zpa_interval_model.json``（训练出的参数 + 留一验证报告），运行时由
      ``zpa_interval.load_model()`` 读取。

候选模型
--------
A. 规则模型（可解释，跑得快）：显著 dip = depth >= max(floor_db, depth_rel × 本文件
   最大深度)；显著 dip 按谐振位置跨层连成轨迹（可跨过 max_gap 层 dip 太浅的层）；
   轨迹段 >= min_points 层；有效区间 = 这些段的跨度（与人工判据一致：区间是
   "谐振在窗口内"的跨度，不是逐层的 dip 强度）。
B. 逐层逻辑回归（sklearn）：特征 = 逐层 dip 强度/相对强度/是否在窗口内/邻域上下文，
   标签 = 该层是否落在人工区间内（区间边界附近一层视为模糊、不参与训练）。

评估：留一交叉验证（LOO）——用 4 张图选参数，在第 5 张图上测。
误差 = 预测区间端点与人工端点的偏差（以 zpa 栅格步长为单位，1 = 差一层）。

用法：
    python train_zpa_interval.py [DataVault 目录]
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import zpa_interval as zi                      # noqa: E402
from zpa_interval_labels import LABELS, load_labeled_files  # noqa: E402

DEFAULT_SESSION = Path(r"D:\DataVault\LQHL.dir\test.dir\20261113_20qubits.dir")
MODEL_FILE = HERE / "zpa_interval_model.json"

GRID = dict(
    depth_rel=[round(x, 2) for x in np.arange(0.10, 0.61, 0.02)],
    depth_floor_db=[0.5, 1.0, 1.5, 2.0],
    tol_mhz=[0.4, 0.6, 1.0, 1.5, 2.0],
    max_gap=[0, 1, 2, 3, 4, 5, 6],
    min_points=[2, 3],
    min_chain_rel=[0.0, 0.2, 0.3, 0.4, 0.5],
)
ENSEMBLE_K = 200


# ── 标签 → 逐层 y / 掩码 ────────────────────────────────────────────────────
def layer_labels(z: np.ndarray, lo: float, hi: float, step: float):
    """(+1 inside, -1 clearly outside, 0 ambiguous at the hand-drawn border)."""
    y = np.zeros(len(z), dtype=int)
    for i, zz in enumerate(z):
        if lo <= zz <= hi:
            y[i] = 1
        elif zz < lo - step / 2 or zz > hi + step / 2:
            y[i] = -1
    else:
        pass
    return y


def interval_error(pred, human, step):
    """端点偏差，单位 = zpa 栅格步长。"""
    if pred is None:
        return float("inf")
    return (abs(pred[0] - human[0]) + abs(pred[1] - human[1])) / step


def loss_for(err):
    """人工标注是目测的，~0.5 层以内算平局；超过 1 层重罚。

    这样目标函数不会为了 0.2 V 的差别去换参数，LOO 才稳。
    """
    if err == float("inf"):
        return 100.0
    return err + 3.0 * max(0.0, err - 1.0)


# ── 模型 A：规则模型（特征只算一次，参数搜索走快路径）──────────────────────
class FileCache:
    def __init__(self, f, z, db):
        self.f, self.z, self.db = f, z, db
        self.step = float(np.median(np.diff(z)))
        self.feats = zi.layer_features(f, db, zi.DEFAULT_EDGE_TOL_MHZ)
        self.max_depth = max(ft["depth_db"] for ft in self.feats)

    def predict(self, params):
        thresh = max(params["depth_floor_db"], params["depth_rel"] * self.max_depth)
        dips_per_layer = [[d for d in ft["dips"] if d["depth_db"] >= thresh]
                          for ft in self.feats]
        chains = zi._chain(dips_per_layer, params["tol_mhz"], params["min_points"],
                           params["max_gap"])
        rel = params.get("min_chain_rel", 0.0)
        if rel > 0 and self.max_depth > 0:
            chains = [br for br in chains
                      if max(c["depth_db"] for c in br["chain"]) >= rel * self.max_depth]
        if not chains:
            return None
        lo = min(self.z[br["chain"][0]["layer"]] for br in chains)
        hi = max(self.z[br["chain"][-1]["layer"]] for br in chains)
        return [float(lo), float(hi)]


def rank_params(caches, humans, top=None):
    """所有参数组合按 (最差损失, 总损失) 排序。"""
    keys = list(GRID)
    scored = []
    for combo in product(*(GRID[k] for k in keys)):
        params = dict(zip(keys, combo))
        losses = [loss_for(interval_error(caches[n].predict(params), humans[n],
                                          caches[n].step)) for n in caches]
        # 平局时取更宽的跟踪容差（tol 越大越好）、更小的 gap（少切碎区间）、
        # 更保守的阈值 —— 人工标注把窗口边缘的谐振和中间平层都算进有效区间，
        # 宁可多包含，不要把轨迹切碎。
        simple = (-params["tol_mhz"], params["max_gap"], params["depth_rel"],
                  params["depth_floor_db"], params["min_chain_rel"])
        scored.append((max(losses), sum(losses), simple, params))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    if top:
        scored = scored[:top]
    return [(a, b, d) for a, b, c, d in scored]


def fit_rule(caches, humans):
    """最小化训练集损失；平局时先看最差的一张图（minimax），再按语义偏好挑。

    minimax 而不是单纯求和：训练集只有 4~5 张图，求和会把某一张图的偏差
    摊到别张图上，留一验证时容易翻车。
    """
    scored = rank_params(caches, humans)
    params = scored[0][2]
    err = sum(interval_error(caches[n].predict(params), humans[n], caches[n].step)
              for n in caches)
    return params, err


# ── 模型 B：逐层逻辑回归 ──────────────────────────────────────────────────
def feature_matrix(c: FileCache):
    rows = []
    depths = np.array([ft["depth_db"] for ft in c.feats])
    maxd = max(depths.max(), 1e-6)
    n = len(depths)
    step_mhz = float(np.median(np.diff(c.f)) * 1000)
    for i, ft in enumerate(c.feats):
        lo, hi = max(0, i - 2), min(n, i + 3)
        ctx = float(depths[lo:hi].max())
        dip = ft["dips"][0] if ft["dips"] else None
        best = max(ft["dips"], key=lambda d: d["depth_db"], default=None)
        idx = best["index"] if best else -1
        dist_edge = min(idx, len(c.f) - 1 - idx) * step_mhz if idx >= 0 else 0.0
        rows.append([
            (best["depth_db"] if best else 0.0) / maxd,
            float(np.log1p(best["depth_db"] if best else 0.0)),
            1.0 if (best and best["interior"]) else 0.0,
            dist_edge,
            ctx / maxd,
            float(np.log1p(maxd)),
            0.0,
        ])
    X = np.array(rows, dtype=float)
    noise = []
    for j in range(c.db.shape[1]):
        col = c.db[:, j]
        col = col[np.isfinite(col)]
        noise.append(float(np.std(np.diff(col, 2))) if len(col) > 2 else 0.0)
    X[:, 6] = np.array(noise) / maxd
    return X


def fit_logreg(caches, humans, train_names, test_name):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    Xs, ys = [], []
    for name in train_names:
        c = caches[name]
        y = layer_labels(c.z, humans[name][0], humans[name][1], c.step)
        keep = y != 0
        Xs.append(feature_matrix(c)[keep])
        ys.append(y[keep])
    X = np.vstack(Xs)
    y = np.concatenate(ys)
    if len(np.unique(y)) < 2:
        return None
    clf = make_pipeline(StandardScaler(),
                        LogisticRegression(max_iter=5000, class_weight="balanced"))
    clf.fit(X, y)
    p = clf.predict_proba(feature_matrix(caches[test_name]))[:, 1]
    idx = np.where(p >= 0.5)[0]
    if len(idx) == 0:
        return None
    z = caches[test_name].z
    return [float(z[idx[0]]), float(z[idx[-1]])]


# ── 主流程 ────────────────────────────────────────────────────────────────
def main():
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SESSION
    caches, humans = {}, {}
    for name, path, human in load_labeled_files(directory):
        f, z, db = zi.load_grid(path)
        caches[name] = FileCache(f, z, db)
        humans[name] = human
        print("loaded %-34s label=(%.2f, %.2f)  nf=%d nz=%d  max_depth=%.2f dB" %
              (name, human[0], human[1], len(f), len(z), caches[name].max_depth))
    if not caches:
        print("没有可用的标注文件（目录：%s）" % directory)
        return 2

    names = list(caches)
    params, err = fit_rule(caches, humans)
    print("\n=== 规则模型（全量训练）===")
    print("params:", json.dumps(params, ensure_ascii=False))
    print("总端点误差 = %.2f 层" % err)
    for n in names:
        p = caches[n].predict(params)
        print("  %-34s 人工(%.2f, %.2f) -> %s  误差 %.2f 层"
              % (n, humans[n][0], humans[n][1],
                 "无" if p is None else "(%.2f, %.2f)" % (p[0], p[1]),
                 interval_error(p, humans[n], caches[n].step)))

    print("\n=== 规则模型（留一验证）===")
    loo = []
    for held in names:
        train = [n for n in names if n != held]
        p, _ = fit_rule({n: caches[n] for n in train}, {n: humans[n] for n in train})
        pred = caches[held].predict(p)
        e = interval_error(pred, humans[held], caches[held].step)
        loo.append((held, pred, e, p))
        print("  %-34s 人工(%.2f, %.2f) -> %s  误差 %.2f 层"
              % (held, humans[held][0], humans[held][1],
                 "无" if pred is None else "(%.2f, %.2f)" % (pred[0], pred[1]), e))
    loo_mean = sum(x[2] for x in loo) / len(loo)
    print("  LOO 平均误差 = %.2f 层" % loo_mean)

    print("\n=== 逐层逻辑回归（留一验证）===")
    lr_err = []
    for held in names:
        train = [n for n in names if n != held]
        pred = fit_logreg(caches, humans, train, held)
        e = interval_error(pred, humans[held], caches[held].step)
        lr_err.append(e)
        print("  %-34s 人工(%.2f, %.2f) -> %s  误差 %.2f 层"
              % (held, humans[held][0], humans[held][1],
                 "无" if pred is None else "(%.2f, %.2f)" % (pred[0], pred[1]), e))
    lr_mean = sum(lr_err) / len(lr_err)
    print("  LOO 平均误差 = %.2f 层" % lr_mean)

    print("\n=== 参数集成（留一验证）===")
    ensemble_params = []
    ens_loo = []
    for held in names:
        train = [n for n in names if n != held]
        scored = rank_params({n: caches[n] for n in train}, {n: humans[n] for n in train})
        top = [p for _, _, p in scored[:ENSEMBLE_K]]
        cand = []
        for p in top:
            pr = caches[held].predict(p)
            if pr:
                cand.append(pr)
        if cand:
            med = (float(np.median([c[0] for c in cand])), float(np.median([c[1] for c in cand])))
            pred = list(min(cand, key=lambda c: abs(c[0] - med[0]) + abs(c[1] - med[1])))
        else:
            pred = None
        e = interval_error(pred, humans[held], caches[held].step)
        ens_loo.append(e)
        print("  %-34s 人工(%.2f, %.2f) -> %s  误差 %.2f 层"
              % (held, humans[held][0], humans[held][1],
                 "无" if pred is None else "(%.2f, %.2f)" % (pred[0], pred[1]), e))
    ens_loo_mean = sum(ens_loo) / len(ens_loo)
    print("  LOO 平均误差 = %.2f 层" % ens_loo_mean)

    # 落盘用的集成：全量训练下最好的 K 组参数
    scored_all = rank_params(caches, humans)
    ensemble_params = [p for _, _, p in scored_all[:ENSEMBLE_K]]
    for n in names:
        cand = [caches[n].predict(p) for p in ensemble_params]
        cand = [c for c in cand if c]
        if cand:
            med = (float(np.median([c[0] for c in cand])), float(np.median([c[1] for c in cand])))
            pick = min(cand, key=lambda c: abs(c[0] - med[0]) + abs(c[1] - med[1]))
            print("  集成 %-34s -> (%.2f, %.2f)   [中位数 (%.2f, %.2f)]"
                  % (n, pick[0], pick[1], med[0], med[1]))

    payload = dict(
        trained_at="2026-11-13",
        source="zpa_interval_labels.json（现场人工标注 5 张）",
        method=("rule: significant dip = depth >= max(floor, rel*file_max); "
                "轨迹跨层连接 + 取各段跨度；运行时用 params，ensemble 只报一致性"),
        params=params,
        ensemble=ensemble_params,
        training=dict(total_endpoint_error_layers=float(err),
                      files=[dict(file=n, human=list(humans[n]),
                                  predicted=caches[n].predict(params),
                                  error_layers=float(interval_error(
                                      caches[n].predict(params), humans[n], caches[n].step)))
                             for n in names],
                      loo=[dict(file=h, human=list(humans[h]), predicted=p,
                                error_layers=float(e), params=pp)
                           for h, p, e, pp in loo],
                      loo_mean_error_layers=float(loo_mean),
                      logreg_loo_mean_error_layers=float(lr_mean),
                      ensemble_size=len(ensemble_params),
                      ensemble_loo_mean_error_layers=float(ens_loo_mean)),
    )
    MODEL_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print("\n模型已写入 %s" % MODEL_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

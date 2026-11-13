"""ZPA interval extraction for S21ZPA maps (offline, never touches hardware).

What the human labels say (2026-11-13, session 20261113_20qubits)
-----------------------------------------------------------------
Five S21ZPA maps were labelled by hand ("which ZPA values are usable?"):

    00044 q21_5  ->  (-2.0, 2.0)
    00047 q11_15 ->  (-2.0, 1.0)
    00049 q13_13 ->  (-2.6, 3.0)
    00055 q11_15 ->  (-3.0, 2.0)
    00059 q19_7  ->  ( 0.0, 3.0)

Looking at the raw traces, the label is the **span over which the readout
resonance is inside the frequency window**: the dip enters the window at one
ZPA edge, sweeps across it and leaves again, and the labelled interval is
exactly that span (rounded by hand to the nearest 0.2 V).

That is a *trajectory* property, not a per-layer property: in 00049 the flat
middle layers (-0.6 .. 1.0 V, no dip at all) are inside the labelled interval,
because the resonance is out of the window there but the range as a whole is
still the usable one.

Algorithm
---------
1. per layer: every local minimum, depth measured against the layer's p90
   (off-resonance) level - a median baseline under-measures broad dips;
2. a dip is *significant* when
       depth >= max(depth_floor_db, depth_rel * max_depth_in_file)
   (the relative part is what the training on the human labels produced:
   each file has its own contrast scale, an absolute threshold does not
   transfer);
3. significant dips are chained across ZPA layers by resonance position
   (|Δf| <= tol_mhz) - window-edge dips are allowed, they are how a resonance
   enters and leaves the readout window;
4. a chain with >= min_points layers is a usable segment; the overall valid
   range is the hull over those segments (the hand labels do the same);
5. the candidate operating point is the layer with the best depth+centrality
   score (NOT a certified sweet point).

Everything here is pure numpy on an HDF5 file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import h5py
import numpy as np

from dv_open import open_datavault

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "zpa_interval_model.json"

# ── defaults (overridable per call; the trained model overrides them) ───────
DEFAULT_MIN_DEPTH_DB = 1.0      # absolute dip depth floor [dB]
DEFAULT_DEPTH_REL = 0.30        # significant = depth >= rel * file max depth
DEFAULT_MIN_POINTS = 2          # consecutive ZPA layers that must be covered
DEFAULT_TOL_MHZ = 1.5           # max resonance movement between two layers
DEFAULT_MAX_GAP = 2             # layers the trajectory may skip (dip too shallow)
DEFAULT_MIN_CHAIN_REL = 0.0     # a chain must reach this fraction of the file max depth
DEFAULT_EDGE_TOL_MHZ = 0.6      # a dip this close to the window edge is "edge"
MAX_BRANCHES = 5                # branches reported per file


def load_model(path=None) -> Dict[str, Any]:
    """Load the trained model (falls back to the built-in defaults)."""
    p = Path(path) if path else MODEL_PATH
    model: Dict[str, Any] = dict(depth_rel=DEFAULT_DEPTH_REL,
                                 depth_floor_db=DEFAULT_MIN_DEPTH_DB,
                                 min_points=DEFAULT_MIN_POINTS,
                                 tol_mhz=DEFAULT_TOL_MHZ,
                                 max_gap=DEFAULT_MAX_GAP,
                                 min_chain_rel=DEFAULT_MIN_CHAIN_REL,
                                 edge_tol_mhz=DEFAULT_EDGE_TOL_MHZ,
                                 ensemble=[])
    try:
        trained = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return model
    if isinstance(trained, dict):
        if isinstance(trained.get("params"), dict):
            model.update({k: v for k, v in trained["params"].items() if k in model})
        if isinstance(trained.get("ensemble"), list):
            model["ensemble"] = trained["ensemble"]
    return model


def load_grid(path) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (freq GHz, zpa, amplitude dB matrix[nf, nz])."""
    with open_datavault(path) as h:
        a = h["DataVault"][()]
    f = np.unique(a["f0"])
    z = np.unique(a["f1"])
    m = np.full((len(f), len(z)), np.nan)
    m[np.searchsorted(f, a["f0"]), np.searchsorted(z, a["f1"])] = a["f2"]
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 20 * np.log10(np.where(m > 0, m, np.nan))
    return f, z, db


def layer_dips(col: np.ndarray, freq: np.ndarray, edge_tol_mhz: float
               ) -> List[Dict[str, Any]]:
    """Every local minimum of one layer with its depth against the p90 level."""
    n = len(col)
    if n < 3 or not np.isfinite(col).any():
        return []
    finite = col[np.isfinite(col)]
    baseline = float(np.percentile(finite, 90))
    step_mhz = float(np.median(np.diff(freq)) * 1000) if n > 1 else 0.0

    dips: List[Dict[str, Any]] = []
    for k in range(n):
        v = col[k]
        if not np.isfinite(v):
            continue
        left = col[k - 1] if k > 0 else np.nan
        right = col[k + 1] if k < n - 1 else np.nan
        is_min = ((k == 0 or (np.isfinite(left) and v <= left)) and
                  (k == n - 1 or (np.isfinite(right) and v <= right)))
        if not is_min:
            continue
        depth = baseline - float(v)
        dist_edge_mhz = min(k, n - 1 - k) * step_mhz
        pos = float(k)
        if 0 < k < n - 1 and np.isfinite(left) and np.isfinite(right):
            denom = float(left) - 2 * float(v) + float(right)
            if abs(denom) > 1e-12:
                delta = 0.5 * (float(left) - float(right)) / denom
                pos = k + float(np.clip(delta, -1.0, 1.0))
        dips.append(dict(index=int(k), position=float(pos),
                         freq=float(freq[k]),
                         freq_interp=float(np.interp(pos, np.arange(n), freq)),
                         depth_db=float(depth),
                         interior=bool(0 < k < n - 1),
                         edge=bool(dist_edge_mhz <= edge_tol_mhz),
                         dist_edge_mhz=float(dist_edge_mhz)))
    return dips


def layer_features(freq: np.ndarray, db: np.ndarray, edge_tol_mhz: float
                   ) -> List[Dict[str, Any]]:
    """Per-layer summary used by the recogniser and by the trainer."""
    out: List[Dict[str, Any]] = []
    nz = db.shape[1] if db.ndim > 1 else 1
    for j in range(nz):
        col = db[:, j]
        dips = layer_dips(col, freq, edge_tol_mhz)
        best = max(dips, key=lambda d: d["depth_db"], default=None)
        out.append(dict(dips=dips,
                        depth_db=float(best["depth_db"]) if best else 0.0,
                        freq_interp=float(best["freq_interp"]) if best else float("nan"),
                        index=int(best["index"]) if best else -1,
                        interior=bool(best["interior"]) if best else False,
                        dist_edge_mhz=float(best["dist_edge_mhz"]) if best else 0.0,
                        n_dips=len(dips)))
    return out


def _chain(dips_per_layer: Sequence[Sequence[Dict[str, Any]]], tol_mhz: float,
           min_points: int, max_gap: int = 0) -> List[Dict[str, Any]]:
    """Chain dips across layers, following the resonance.

    Edge dips take part on purpose: a resonance that leaves the readout window
    is still the same resonance, and the hand labels include those layers.

    ``max_gap`` layers may be skipped inside a chain (layers whose dip is too
    shallow to be significant).  Without it the trajectory breaks exactly where
    the human labels say the interval is continuous.
    """
    nz = len(dips_per_layer)
    chains: List[Dict[str, Any]] = []
    for j0 in range(nz):
        for d0 in dips_per_layer[j0]:
            chain = [dict(d0, layer=j0)]
            j = j0
            while True:
                nxt = None
                nxt_layer = None
                for step_layers in range(1, max_gap + 2):
                    jj = j + step_layers
                    if jj >= nz:
                        break
                    for cand in dips_per_layer[jj]:
                        if abs(cand["freq_interp"] - chain[-1]["freq_interp"]) * 1000 > tol_mhz:
                            continue
                        if nxt is None or cand["depth_db"] > nxt["depth_db"]:
                            nxt = cand
                            nxt_layer = jj
                if nxt is None:
                    break
                chain.append(dict(nxt, layer=nxt_layer))
                j = nxt_layer
            if len(chain) >= min_points:
                chains.append(dict(chain=chain, start_layer=j0))
    if not chains:
        return []
    # keep maximal chains only (a chain that is a suffix of a longer one is redundant)
    maximal: List[Dict[str, Any]] = []
    for br in chains:
        chain = br["chain"]
        redundant = any(other is not br and len(other["chain"]) > len(chain) and
                        other["chain"][-len(chain):] == chain for other in chains)
        if not redundant:
            maximal.append(br)
    # one chain per covered layer span, keep the deepest
    by_span: Dict[Tuple[int, ...], Dict[str, Any]] = {}
    for br in maximal:
        key = tuple(c["layer"] for c in br["chain"])
        cur = by_span.get(key)
        if cur is None or (sum(c["depth_db"] for c in br["chain"]) >
                           sum(c["depth_db"] for c in cur["chain"])):
            by_span[key] = br
    unique = list(by_span.values())
    spans = {tuple(c["layer"] for c in br["chain"]) for br in unique}
    keep: List[Dict[str, Any]] = []
    for br in unique:
        s = tuple(c["layer"] for c in br["chain"])
        if not any(o != s and set(s).issubset(set(o)) for o in spans):
            keep.append(br)
    unique = keep
    unique.sort(key=lambda br: (-len(br["chain"]),
                                -float(np.mean([c["depth_db"] for c in br["chain"]]))))
    return unique[:MAX_BRANCHES]


def analyze_intervals(path, min_depth_db: Optional[float] = None,
                       min_points: Optional[int] = None,
                       tol_mhz: Optional[float] = None,
                       edge_tol_mhz: Optional[float] = None,
                       depth_rel: Optional[float] = None,
                       model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract usable ZPA intervals from one S21ZPA file."""
    m = load_model()
    if model:
        m.update({k: v for k, v in model.items() if k in m})
    if min_depth_db is not None:
        m["depth_floor_db"] = min_depth_db
    if min_points is not None:
        m["min_points"] = min_points
    if tol_mhz is not None:
        m["tol_mhz"] = tol_mhz
    if edge_tol_mhz is not None:
        m["edge_tol_mhz"] = edge_tol_mhz
    if depth_rel is not None:
        m["depth_rel"] = depth_rel
    # an explicit override is the caller's decision - the trained ensemble
    # would silently ignore it
    if any(x is not None for x in (min_depth_db, min_points, tol_mhz,
                                   edge_tol_mhz, depth_rel)):
        m["ensemble"] = []

    f, z, db = load_grid(path)
    return analyze_grid(f, z, db, m, source=str(path))


def _branches_for(feats, max_depth: float, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Chains of significant dips for one parameter set."""
    thresh = max(float(params["depth_floor_db"]), float(params["depth_rel"]) * max_depth)
    dips_per_layer = [[d for d in ft["dips"] if d["depth_db"] >= thresh] for ft in feats]
    branches = _chain(dips_per_layer, float(params["tol_mhz"]), int(params["min_points"]),
                      int(params.get("max_gap", DEFAULT_MAX_GAP)))
    # a chain that never gets close to the file's best dip is noise, not the
    # readout resonance - the human labels never include such layers
    chain_rel = float(params.get("min_chain_rel", DEFAULT_MIN_CHAIN_REL))
    if chain_rel > 0 and max_depth > 0:
        branches = [br for br in branches
                    if max(c["depth_db"] for c in br["chain"]) >= chain_rel * max_depth]
    return branches


def _range_of(branches, z) -> Optional[List[float]]:
    if not branches:
        return None
    lo = min(z[br["chain"][0]["layer"]] for br in branches)
    hi = max(z[br["chain"][-1]["layer"]] for br in branches)
    return [float(lo), float(hi)]


def analyze_grid(f: np.ndarray, z: np.ndarray, db: np.ndarray,
                 m: Optional[Dict[str, Any]] = None,
                 source: str = "<grid>") -> Dict[str, Any]:
    """Same as :func:`analyze_intervals` but on an already loaded grid."""
    m_extra = m or {}
    m = dict(load_model())
    if m_extra:
        m.update({k: v for k, v in m_extra.items() if k in m})
    nfreq, nz = len(f), len(z)
    feats = layer_features(f, db, m["edge_tol_mhz"])
    max_depth = max((ft["depth_db"] for ft in feats), default=0.0)
    thresh = max(float(m["depth_floor_db"]), float(m["depth_rel"]) * max_depth)

    branches = _branches_for(feats, max_depth, m)

    # Trained ensemble: the top parameter sets of the training run.  The
    # primary parameters decide the answer, the ensemble only reports how
    # stable that answer is (agreement / spread) - picking a member would
    # silently drop a secondary branch that the hand labels do include
    # (see 00049: two branches, one interval).
    ensemble = m.get("ensemble") or []
    ensemble_used = 0
    ensemble_agreement = None
    ensemble_spread = None
    if len(ensemble) > 1:
        base = _range_of(branches, z)
        los, his = [], []
        for p in ensemble:
            rng = _range_of(_branches_for(feats, max_depth, p), z)
            if rng:
                los.append(rng[0])
                his.append(rng[1])
        if los and base:
            ensemble_used = len(los)
            step = float(np.median(np.diff(z))) if len(z) > 1 else 0.4
            agree = sum(1 for a, b in zip(los, his)
                        if abs(a - base[0]) <= step + 1e-9 and abs(b - base[1]) <= step + 1e-9)
            ensemble_agreement = float(agree) / len(los)
            ensemble_spread = [float(min(los)), float(max(his))]

    intervals: List[Dict[str, Any]] = []
    for br in branches:
        chain = br["chain"]
        layers = [c["layer"] for c in chain]
        zs = np.array([z[l] for l in layers])
        scores = []
        for c in chain:
            centrality = 1.0 - min(c["dist_edge_mhz"], 10.0) / 10.0
            scores.append(c["depth_db"] / (1.0 + c["depth_db"]) + centrality)
        best = int(np.argmax(scores))
        c = chain[best]
        freqs = np.array([x["freq_interp"] for x in chain])
        slopes = np.abs(np.gradient(freqs, zs)) if len(zs) > 1 else np.array([0.0])
        intervals.append(dict(
            sampled_zpa_interval=[float(zs[0]), float(zs[-1])],
            point_count=int(len(chain)),
            candidate_zpa=float(z[chain[best]["layer"]]),
            resonance_frequency_native=float(c["freq_interp"]),
            candidate_depth_db=float(c["depth_db"]),
            median_prominence_db=float(np.median([x["depth_db"] for x in chain])),
            median_slope_mhz_per_v=float(np.median(slopes) * 1000),
            all_edge=bool(all(x["edge"] for x in chain)),
            selection="branch_depth_centrality_not_certified_sweetpoint",
            boundary_resolution=float(np.median(np.diff(zs))) if len(zs) > 1 else None,
            layers=[int(x["layer"]) for x in chain],
            depth_trace=[round(float(x["depth_db"]), 2) for x in chain],
            freq_trace_mhz=[round(float((x["freq_interp"] - f.min()) * 1000), 3) for x in chain],
        ))
    intervals.sort(key=lambda q: (-q["point_count"], -q["median_prominence_db"]))

    # overall valid range = hull over the segments (what the hand labels do)
    valid_range = _range_of(branches, z)

    points: List[Dict[str, Any]] = []
    for j, zz in enumerate(z):
        ft = feats[j]
        chosen = ft["dips"][0] if ft["dips"] else None
        best_dip = max(ft["dips"], key=lambda d: d["depth_db"], default=None)
        if not ft["dips"]:
            reason = "no_dip_above_threshold"
        elif not best_dip["interior"]:
            reason = "dip_at_window_edge"
        else:
            reason = "supported_interior_dip"
        points.append(dict(zpa=float(zz),
                           supported=bool(ft["dips"]),
                           reason=reason,
                           resonance_frequency_native=float(best_dip["freq_interp"]) if best_dip else None,
                           depth_db=float(best_dip["depth_db"]) if best_dip else 0.0,
                           depth_rel=float(best_dip["depth_db"] / max_depth) if (best_dip and max_depth > 0) else 0.0,
                           n_dips=int(ft["n_dips"]),
                           frequency_index=int(best_dip["index"]) if best_dip else int(np.nanargmin(db[:, j]))))

    return dict(file=source, n_freq=nfreq, n_zpa=nz,
                zpa_min=float(z.min()), zpa_max=float(z.max()),
                zpa_step=float(np.median(np.diff(z))) if nz > 1 else None,
                freq_min_native=float(f.min()), freq_max_native=float(f.max()),
                freq_step_mhz=float(np.median(np.diff(f)) * 1000) if nfreq > 1 else None,
                max_depth_db=float(max_depth),
                significance_threshold_db=float(thresh),
                valid_zpa_range=valid_range,
                ensemble_size=int(ensemble_used),
                ensemble_agreement=ensemble_agreement,
                ensemble_spread=ensemble_spread,
                intervals=intervals, points=points,
                params=dict(depth_floor_db=m["depth_floor_db"],
                            depth_rel=m["depth_rel"],
                            min_points=m["min_points"],
                            max_gap=m.get("max_gap", DEFAULT_MAX_GAP),
                            min_chain_rel=m.get("min_chain_rel", DEFAULT_MIN_CHAIN_REL),
                            tol_mhz=m["tol_mhz"], edge_tol_mhz=m["edge_tol_mhz"]))


def pick_interval(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not result.get("intervals"):
        return None
    return max(result["intervals"],
               key=lambda q: (q["point_count"], q["median_prominence_db"]))

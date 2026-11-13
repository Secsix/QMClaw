"""ZPA interval extraction for S21ZPA maps (offline, never touches hardware).

Why this replaces the "argmin + find_peaks prominence" rule
----------------------------------------------------------
1. One S21ZPA layer can hold **several resonances** (readout cavity + qubit
   transition).  Taking the single global minimum per layer makes the ridge
   jump between branches, which shreds the interval into fragments.
2. `find_peaks(prominence=...)` measures against the **immediate neighbours**.
   With a 0.2 MHz sample step a broad dip has a local prominence near 0 dB even
   when it is 5-8 dB deep, so real dips were rejected by a 1 dB threshold.
3. A dip sitting on the window edge is not "unusable data" - it is a resonance
   that left the readout window, and it can still be *tracked* across layers.

Algorithm
---------
* per layer: find every local minimum, measure its depth against the layer's
  median baseline (robust) and against the window edges,
* track branches across consecutive ZPA layers by resonance position
  (|Δf| <= tol), keeping the longest/deepest chain (dynamic programming),
* a branch spanning >= min_points consecutive layers is a usable interval;
  the candidate operating point is the layer with the best
  depth + centrality score.

Everything here is pure numpy/scipy on an HDF5 file.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import h5py
import numpy as np

# ── defaults (all overridable per call) ─────────────────────────────────────
DEFAULT_MIN_DEPTH_DB = 1.0      # dip depth vs the layer's off-resonance level
DEFAULT_MIN_POINTS = 3          # consecutive ZPA layers that must be covered
DEFAULT_TOL_MHZ = 1.5           # max resonance movement between two layers
DEFAULT_EDGE_TOL_MHZ = 0.6      # a dip this close to the window edge is "edge"
MAX_BRANCHES = 5                # branches reported per file


def load_grid(path) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (freq GHz, zpa, amplitude dB matrix[nf, nz])."""
    with h5py.File(path, "r", locking=False) as h:
        ds = h["DataVault"]
        a = ds[()]
    f = np.unique(a["f0"])
    z = np.unique(a["f1"])
    m = np.full((len(f), len(z)), np.nan)
    m[np.searchsorted(f, a["f0"]), np.searchsorted(z, a["f1"])] = a["f2"]
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 20 * np.log10(np.where(m > 0, m, np.nan))
    return f, z, db


def _layer_dips(col: np.ndarray, freq: np.ndarray, min_depth_db: float,
                edge_tol_mhz: float) -> List[Dict[str, Any]]:
    """All local minima of one layer, with robust depth estimates.

    Baseline is the 90th percentile of the layer (off-resonance transmission
    level).  A median baseline is wrong here: a broad dip occupies several
    samples and drags the median down, which made 8 dB dips look like 1.9 dB.
    """
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
        # sub-sample position (parabolic around the discrete minimum)
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
    strong = [d for d in dips if d["depth_db"] >= min_depth_db]
    return strong or []


def _track_branches(dips_per_layer: Sequence[List[Dict[str, Any]]],
                    tol_mhz: float) -> List[Dict[str, Any]]:
    """Chain dips across consecutive layers: follow the resonance as it moves.

    Only **interior** dips take part: a minimum sitting exactly on the window
    boundary is the tail of a resonance that already left the readout window,
    and stitching those tails together invents branches that do not exist.
    """
    nz = len(dips_per_layer)
    branches: List[Dict[str, Any]] = []
    for j0 in range(nz):
        for d0 in dips_per_layer[j0]:
            if not d0["interior"]:
                continue
            chain = [dict(d0, layer=j0)]
            j = j0
            while True:
                nxt = None
                for cand in (dips_per_layer[j + 1] if j + 1 < nz else []):
                    if not cand["interior"]:
                        continue
                    if abs(cand["freq_interp"] - chain[-1]["freq_interp"]) * 1000 > tol_mhz:
                        continue
                    if nxt is None or cand["depth_db"] > nxt["depth_db"]:
                        nxt = cand
                if nxt is None:
                    break
                chain.append(dict(nxt, layer=j + 1))
                j += 1
            branches.append(dict(chain=chain, start_layer=j0))
    # keep only maximal chains (a chain that is a suffix of a longer one is redundant)
    maximal: List[Dict[str, Any]] = []
    for br in branches:
        chain = br["chain"]
        redundant = False
        for other in branches:
            if other is br or len(other["chain"]) <= len(chain):
                continue
            if other["chain"][-len(chain):] == chain:
                redundant = True
                break
        if not redundant:
            maximal.append(br)
    # drop exact duplicates and keep only the best chain per covered layer span
    by_span: Dict[Tuple[int, ...], Dict[str, Any]] = {}
    for br in maximal:
        key = tuple(c["layer"] for c in br["chain"])
        cur = by_span.get(key)
        if cur is None or (sum(c["depth_db"] for c in br["chain"]) >
                           sum(c["depth_db"] for c in cur["chain"])):
            by_span[key] = br
    unique = list(by_span.values())
    # a branch whose layer span is contained in a longer branch is redundant
    spans = {tuple(c["layer"] for c in br["chain"]) for br in unique}
    keep: List[Dict[str, Any]] = []
    for br in unique:
        s = tuple(c["layer"] for c in br["chain"])
        contained = any(o != s and set(s).issubset(set(o)) for o in spans)
        if not contained:
            keep.append(br)
    unique = keep
    # rank: longest first, then deepest; keep a handful so one noisy file cannot
    # flood the report with a hundred near-identical branches
    unique.sort(key=lambda br: (-len(br["chain"]),
                                -float(np.mean([c["depth_db"] for c in br["chain"]]))))
    return unique[:MAX_BRANCHES]


def analyze_intervals(path, min_depth_db: float = DEFAULT_MIN_DEPTH_DB,
                       min_points: int = DEFAULT_MIN_POINTS,
                       tol_mhz: float = DEFAULT_TOL_MHZ,
                       edge_tol_mhz: float = DEFAULT_EDGE_TOL_MHZ) -> Dict[str, Any]:
    """Extract usable ZPA intervals from one S21ZPA file."""
    f, z, db = load_grid(path)
    nfreq, nz = len(f), len(z)
    dips_per_layer: List[List[Dict[str, Any]]] = []
    for j in range(nz):
        dips_per_layer.append(_layer_dips(db[:, j], f, min_depth_db, edge_tol_mhz))

    branches = _track_branches(dips_per_layer, tol_mhz)
    intervals: List[Dict[str, Any]] = []
    for br in branches:
        chain = br["chain"]
        if len(chain) < min_points:
            continue
        layers = [c["layer"] for c in chain]
        zs = np.array([z[l] for l in layers])
        # candidate: deepest dip with the best centrality inside the window
        scores = []
        for c in chain:
            centrality = 1.0 - min(c["dist_edge_mhz"], 10.0) / 10.0
            scores.append(c["depth_db"] / (1.0 + c["depth_db"]) + centrality
                          - (0.2 if c["edge"] else 0.0))
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

    points: List[Dict[str, Any]] = []
    for j, zz in enumerate(z):
        col = db[:, j]
        if not np.isfinite(col).any():
            points.append(dict(zpa=float(zz), supported=False, reason="no_valid_amplitude"))
            continue
        dips = dips_per_layer[j]
        interior = [d for d in dips if d["interior"]]
        chosen = max(dips, key=lambda d: d["depth_db"], default=None)
        if chosen is None:
            reason = "no_dip_above_depth_threshold"
        elif not chosen["interior"]:
            reason = "dip_at_window_edge"
        else:
            reason = "supported_interior_dip"
        points.append(dict(zpa=float(zz),
                           supported=bool(interior),
                           reason=reason,
                           resonance_frequency_native=float(chosen["freq_interp"]) if chosen else None,
                           depth_db=float(chosen["depth_db"]) if chosen else 0.0,
                           n_dips=len(dips),
                           frequency_index=int(chosen["index"]) if chosen else int(np.nanargmin(col))))

    return dict(file=str(path), n_freq=nfreq, n_zpa=nz,
                zpa_min=float(z.min()), zpa_max=float(z.max()),
                zpa_step=float(np.median(np.diff(z))) if nz > 1 else None,
                freq_min_native=float(f.min()), freq_max_native=float(f.max()),
                freq_step_mhz=float(np.median(np.diff(f)) * 1000) if nfreq > 1 else None,
                intervals=intervals, points=points,
                params=dict(min_depth_db=min_depth_db, min_points=min_points,
                            tol_mhz=tol_mhz, edge_tol_mhz=edge_tol_mhz))


def pick_interval(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not result.get("intervals"):
        return None
    return max(result["intervals"],
               key=lambda q: (q["point_count"], q["median_prominence_db"]))

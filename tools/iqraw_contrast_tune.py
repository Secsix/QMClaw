"""Readout-contrast tuning loop, driven entirely through the quantum microservice.

Why
---
A qubit whose readout is barely separable (``P(1|prep X) - P(1|prep I)`` small)
cannot be tuned blindly: the pi-pulse amplitude, the drive frequency and the
discriminator all interact.  The recipe is to alternate three semantic
experiment kinds and re-score the readout after every round:

* ``iqraw`` - take IQ clouds and refresh the discriminator
* ``piamp`` - re-optimise the pi-pulse amplitude against the current readout
* ``pidf``  - small frequency-offset scan, pick the drive frequency that gives
  the largest contrast (this is what actually re-centres ``f10``)

This script owns **no** lab knowledge: it never talks to LabRAD, never opens a
DataVault, and never re-implements the contrast formula.  Everything goes
through the service HTTP API:

* ``POST :3003/experiments/run`` - run one semantic experiment kind
* ``POST :3003/qubit/contrast``   - read back the readout contrast (server-side
  scoring, threshold and verdict come from ``server/config/lab_criteria.json``)
* ``POST :3003/qubit/registry``   - read the qubit's register tree

The service's own execution lane is used, so its serial lock, watchdog and
write gate apply as usual (``quantum_service.actuation.allow_execute``).

Usage
-----
    python tools/iqraw_contrast_tune.py --qubit q1
    python tools/iqraw_contrast_tune.py --qubit q1 --rounds 2
    python tools/iqraw_contrast_tune.py --qubit q1 --dry-run

Options
-------
--base-url  quantum service base URL (default http://127.0.0.1:3003)
--df        half-width of the ``pidf`` frequency scan, in GHz (default 0.05)
--timeout   per-step HTTP timeout in seconds (default 900)
--rounds    how many times to replay the whole step sequence (default 1)
--dry-run   print the plan and exit without touching the hardware
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:3003"

#: 语义步骤：kind → params。vendor 的函数名/参数口径在服务端，这里只说"做什么"。
STEP_SEQUENCE = ("iqraw", "piamp", "iqraw", "pidf", "iqraw", "piamp")

#: 只打印这几个寄存器，不把整棵 registry 倒出来
REGISTER_KEYS = ("f10", "f21", "PiGate.amp", "PiGate.length")


# --------------------------------------------------------------------------- HTTP
def _post(base_url: str, path: str, payload: dict, timeout: int) -> dict:
    """POST JSON to the quantum service and return the decoded JSON body."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
            return json.loads(resp.read().decode(errors="replace"))
    except urllib.error.HTTPError as exc:  # 服务端返回了错误信封
        raw = exc.read().decode(errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"error": raw or f"HTTP {exc.code}"}


# ------------------------------------------------------------------------ helpers
def _flatten(node, prefix: str = "") -> dict:
    """Flatten the nested registry tree into ``{"PiGate.amp": 0.4, ...}``."""
    out = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(_flatten(value, path))
            else:
                out[path] = value
    return out


def read_registers(base_url: str, qubit: str, timeout: int) -> dict:
    """Read the qubit's register tree through the service (read-only)."""
    payload = _post(base_url, "/qubit/registry", {"name": qubit}, timeout)
    if payload.get("error") or not payload.get("success", True):
        return {"_error": payload.get("error") or "registry 读取失败"}
    flat = _flatten(payload.get("registry") or {})
    out = {}
    for key in REGISTER_KEYS:
        for candidate in (key, key.split(".")[-1]):
            if candidate in flat:
                out[key] = flat[candidate]
                break
    return out


def run_experiment(base_url: str, kind: str, qubit: str, params: dict, timeout: int) -> dict:
    """Run one semantic experiment kind through the service."""
    payload = _post(
        base_url,
        "/experiments/run",
        {
            "kind": kind,
            "qubit": qubit,
            "params": params,
            "do_plot": False,
            "timeout": timeout,
        },
        timeout,
    )
    return payload


def read_contrast(base_url: str, qubit: str, timeout: int) -> dict:
    """Ask the service to score the latest readout dataset."""
    return _post(base_url, "/qubit/contrast", {"name": qubit, "kind": "IQraw"}, timeout)


def _fmt_registers(regs: dict) -> str:
    if regs.get("_error"):
        return f"registry 读取失败：{regs['_error']}"
    if not regs:
        return "registry 里没找到关心的键"
    return "  ".join(f"{k}={regs[k]}" for k in REGISTER_KEYS if k in regs)


def _fmt_contrast(payload: dict) -> str:
    if payload.get("error"):
        return f"contrast 读取失败：{payload.get('error')}"
    metrics = payload.get("metrics") or {}
    verdict = payload.get("verdict") or {}
    v = verdict.get("verdict") if isinstance(verdict, dict) else verdict
    return (
        f"contrast={payload.get('contrast')} "
        f"(p1_i={metrics.get('p1_i')} p1_x={metrics.get('p1_x')} "
        f"snr={metrics.get('snr')} shots={metrics.get('shots')}) "
        f"passed={payload.get('passed')} threshold={payload.get('threshold')} "
        f"verdict={v}"
    )


def steps_for(df: float) -> list[tuple[str, dict]]:
    """Build the semantic step sequence (kind, params)."""
    steps = []
    for kind in STEP_SEQUENCE:
        params = {"df": df} if kind == "pidf" else {}
        steps.append((kind, params))
    return steps


# ------------------------------------------------------------------------ driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--qubit", default="q1", help="要调的比特名")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="量子服务地址")
    ap.add_argument("--df", type=float, default=0.05, help="pidf 扫描半宽，GHz")
    ap.add_argument("--timeout", type=int, default=900, help="每步 HTTP 超时（秒）")
    ap.add_argument("--rounds", type=int, default=1, help="整个序列重复几轮")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连服务")
    args = ap.parse_args()

    steps = steps_for(args.df)
    if args.dry_run:
        for i, (kind, params) in enumerate(steps, 1):
            print(f"{i}. kind={kind} params={json.dumps(params, ensure_ascii=False)}")
        return 0

    print(f"== 寄存器基线（{args.qubit}）==")
    print("   " + _fmt_registers(read_registers(args.base_url, args.qubit, args.timeout)))

    for round_index in range(1, max(1, args.rounds) + 1):
        print(f"\n== 第 {round_index} 轮 ==")
        for step_index, (kind, params) in enumerate(steps, 1):
            print(f"-- 步骤 {step_index}/{len(steps)}：{kind}")
            result = run_experiment(args.base_url, kind, args.qubit, params, args.timeout)
            if not result.get("success", False):
                print(f"   实验失败：{result.get('error') or result.get('error_code')}")
                continue
            print(f"   {kind} 完成（backend={result.get('backend')}）")
            print("   " + _fmt_contrast(read_contrast(args.base_url, args.qubit, args.timeout)))

    return 0


if __name__ == "__main__":
    sys.exit(main())

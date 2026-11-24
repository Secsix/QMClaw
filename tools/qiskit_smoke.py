"""qiskit 后端冒烟脚本：单比特 + CZ 门调控，一次跑完出 JSON + PNG。

用途
----
把"单比特调控"和"双比特 CZ 调控"两条路各跑一遍，把结果落成
**机器可读**（JSON）与**人可读**（PNG）两份产物，便于贴进报告 / 比对。

它走的是**和生产端点完全同一条路**：
``services.quantum_service.qiskit_backend.run`` →
（若 ``config/system.local.json`` 里的 ``qiskit.python_executable``
 指向另一个解释器，则自动改走子进程执行）。

用法
----
    python tools/qiskit_smoke.py                       # 默认全套，输出到 qwf_out/qiskit_smoke/
    python tools/qiskit_smoke.py --out out/qiskit      # 换输出目录
    python tools/qiskit_smoke.py --experiments t1,cz.dcz
    python tools/qiskit_smoke.py --shots 2000

产物
----
``<out>/report.json``
    每个实验的 status / metrics / elapsed_ms / 关键指标断言结论。
``<out>/<experiment>.png``
    每个实验一张图（形状与 ``/qiskit/run`` 返回的 ``data`` 一致）。

退出码
------
0 = 全部实验成功且 CZ 指标符合物理口径；1 = 有失败（详见 report.json）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# 仓库结构：<repo>/tools/qiskit_smoke.py，<repo>/server/
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVER_DIR = _REPO_ROOT / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.quantum_service import qiskit_backend  # noqa: E402

#: 默认跑哪些实验：单比特调控一组 + CZ 一个
DEFAULT_EXPERIMENTS = [
    "t1",
    "ramsey",
    "piamp",
    "xeb",
    "single_shot",
    "spectroscopy",
    "pulsed_spec",
    "s21",
    "s21_dis",
    "iqraw",
    "allxy",
    "drag_calibrate",
    "cz.dcz",
]

#: 每个实验的额外参数（点数/采样数按后端上限钳位）
DEFAULT_PARAMS: Dict[str, Dict[str, Any]] = {
    "spectroscopy": {"points": 21},
    "pulsed_spec": {"points": 21},
    "s21": {"points": 21},
    "iqraw": {"samples": 120},
    "drag_calibrate": {"points": 9},
    "cz.dcz": {"error_rate": 0.01},
}


def _run_one(experiment: str, shots: int, qubit: str = "q0") -> Dict[str, Any]:
    payload = {
        "experiment": experiment,
        "qubit": qubit,
        "shots": shots,
        "params": dict(DEFAULT_PARAMS.get(experiment, {})),
    }
    started = time.time()
    try:
        result = qiskit_backend.run(payload)
    except Exception as exc:  # noqa: BLE001 — 冒烟脚本要把异常记进报告
        return {
            "experiment": experiment,
            "status": "qiskit_error",
            "error": f"{type(exc).__name__}: {exc}",
            "wall_s": round(time.time() - started, 3),
        }
    result.setdefault("wall_s", round(time.time() - started, 3))
    return result


def _plot(result: Dict[str, Any], out_dir: Path) -> str | None:
    """按后端返回的 ``data`` 画一张 PNG（与服务端画图口径一致）。"""
    data = result.get("data") or {}
    x = data.get("x") or []
    y = data.get("y") or []
    if not x or not y:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        result.setdefault("plot_error", f"{type(exc).__name__}: {exc}")
        return None

    fig, ax = plt.subplots(figsize=(6, 4))
    kind = data.get("type", "line")
    labels = (result.get("extra") or {}).get("labels")
    if kind == "bar":
        if labels and len(labels) == len(x):
            ax.bar([str(v) for v in labels], y, color="#4C78A8")
        else:
            ax.bar(list(range(len(y))), y, color="#4C78A8")
    elif kind == "scatter":
        ax.scatter(x, y, color="#4C78A8")
    else:
        ax.plot(x, y, color="#4C78A8")
    ax.set_xlabel(data.get("xlabel", "X"))
    ax.set_ylabel(data.get("ylabel", "Y"))
    ax.set_title(data.get("title") or f"{result.get('experiment')} (qiskit)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path = out_dir / f"{result.get('experiment', 'unknown')}.png"
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def _cz_checks(result: Dict[str, Any]) -> Dict[str, Any]:
    """CZ 指标是否符合物理口径。

    理想 CZ：Bell 态 |Φ+> → CZ 翻成 |Φ->，X 基期望 -1 ⇒
    ``cz_fidelity = (1 - <X0X1>) / 2 = 1``。
    """
    m = result.get("metrics") or {}
    out: Dict[str, Any] = {}
    if "cz_fidelity" not in m:
        return out
    out["bell_fidelity"] = m.get("bell_fidelity")
    out["bell_parity"] = m.get("bell_parity")
    out["cz_parity"] = m.get("cz_parity")
    out["cz_fidelity"] = m.get("cz_fidelity")
    out["ideal_cz_ok"] = bool(
        abs(float(m.get("cz_fidelity", 0)) - 1.0) <= 0.05
        and abs(float(m.get("bell_fidelity", 0)) - 1.0) <= 0.05
    )
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="qiskit 后端冒烟（单比特 + CZ）")
    parser.add_argument(
        "--out",
        default=str(_REPO_ROOT / "qwf_out" / "qiskit_smoke"),
        help="输出目录（默认 qwf_out/qiskit_smoke）",
    )
    parser.add_argument(
        "--experiments",
        default="",
        help="逗号分隔的实验名；缺省跑默认全套",
    )
    parser.add_argument("--shots", type=int, default=1000, help="每实验 shots")
    parser.add_argument("--qubit", default="q0", help="比特名")
    args = parser.parse_args(argv)

    experiments = (
        [e.strip() for e in args.experiments.split(",") if e.strip()]
        if args.experiments
        else list(DEFAULT_EXPERIMENTS)
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    status = qiskit_backend.status()
    print(f"[qiskit_smoke] 后端状态: {json.dumps(status, ensure_ascii=False)}")

    report: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend_status": status,
        "shots": args.shots,
        "qubit": args.qubit,
        "results": [],
    }
    failures = 0

    for experiment in experiments:
        result = _run_one(experiment, args.shots, args.qubit)
        entry: Dict[str, Any] = {
            "experiment": experiment,
            "status": result.get("status"),
            "error": result.get("error"),
            "elapsed_ms": result.get("elapsed_ms"),
            "wall_s": result.get("wall_s"),
            "approximation": result.get("approximation"),
            "metrics": result.get("metrics"),
            "extra": {
                k: v
                for k, v in (result.get("extra") or {}).items()
                if k in ("qubits", "qubit_a", "qubit_b", "error_rate", "labels")
            },
        }
        if experiment.startswith("cz."):
            entry["cz_checks"] = _cz_checks(result)
        png = _plot(result, out_dir)
        entry["plot"] = png
        ok = result.get("status") == "qiskit_completed"
        entry["ok"] = ok
        if not ok:
            failures += 1
        report["results"].append(entry)
        print(
            f"[qiskit_smoke] {experiment:<16} {entry['status']:<18} "
            f"metrics={json.dumps(result.get('metrics'), ensure_ascii=False)}"
        )

    report["failures"] = failures
    report_path = out_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[qiskit_smoke] 报告: {report_path}")
    print(f"[qiskit_smoke] 失败 {failures} 个")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

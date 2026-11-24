#!/usr/bin/env python
"""DSL → qiskit 全链路冒烟（离线版）。

把一条 .qwf 脚本从**文本**一路推到 **qiskit/Aer 出数**，中间每一环都在本进程里跑：

    .qwf 文本
      → qwf.parse_qwf           解析成 IR
      → dsl.validate             语义校验（比特元数 / coupler 必填 …）
      → qwf.compile.build_node   IR 语句 → 节点 config（fn/qubits/coupler）
      → adapters.lqcs.build_experiment_code
                                 节点 config → 可执行代码（真机形态；CZ 走类方法）
      → offline_simulator.parse_experiment_code_multi
                                 代码 → (实验名, 比特, 参数)   ← 宿主真正认的那一步
      → qiskit_backend.run      真跑 Aer（子进程，用 config/system.local.json 的解释器）

这条链正是宿主 `workflow_service → quantum_service` 走的那条（宿主把 IR 编译成
config、把 config 拼成代码字符串、再按代码解析回实验名）。本脚本把它拉平成
一次调用，因此**不需要起任何微服务**。

用法（仓库根目录）：

    python tools/qiskit_dsl_smoke.py                       # 跑 cz.dcz
    python tools/qiskit_dsl_smoke.py --script swap          # 换脚本
    python tools/qiskit_dsl_smoke.py --list                # 看有哪些脚本
    python tools/qiskit_dsl_smoke.py --shots 2000

退出码：0 = 全链打通；1 = 某一环断了（报告里能看到断在哪一步）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = REPO_ROOT / "server"
SCRIPTS_DIR = REPO_ROOT / "qwf_scripts" / "experiments"

# 门面 setup() 会注入这几个环境变量，指向宿主自己的配置/数据目录：
# QWF_CONFIG_DIR / QWF_VOCAB_PATH / QWF_DATA_DIR
os.environ.setdefault("QWF_CONFIG_DIR", str(SERVER_ROOT / "config"))
os.environ.setdefault("QWF_VOCAB_PATH", str(SERVER_ROOT / "config" / "experiment_configs.json"))
os.environ.setdefault("QWF_DATA_DIR", str(SERVER_ROOT / "data" / "workflow-runs"))

# 宿主服务包（qiskit_backend / offline_simulator / 门面 dsl）
sys.path.insert(0, str(SERVER_ROOT))

# Windows 控制台默认 GBK，中文/符号会炸；统一改成 UTF-8（errors=replace 兜底）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


def _resolve_factory(env: Dict[str, Any]):
    """给 compile.build_node 用的 resolve：把 `$env.xxx` 解成 env 里的值。"""

    def resolve(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$env."):
            return env.get(value[len("$env."):])
        if isinstance(value, list):
            return [resolve(v) for v in value]
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        return value

    return resolve


def _stage(name: str, ok: bool, **extra: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {"stage": name, "ok": bool(ok)}
    row.update(extra)
    return row


def run_script(name: str, shots: int, verbose: bool = False) -> Dict[str, Any]:
    """跑一条 .qwf 的完整链路，返回一份结构化报告。"""
    from services.quantum_service.offline_simulator import ExperimentSimulator
    from services.quantum_service import qiskit_backend
    from services.workflow_service.dsl import compile as dsl_compile
    from services.workflow_service.dsl.validate import error_list
    from services.workflow_service.dsl.validate import validate as dsl_validate
    from services.workflow_service.dsl.backends.lqcs import build_experiment_code
    import qwf

    report: Dict[str, Any] = {"script": name, "stages": []}
    stages = report["stages"]

    # ── 0. 读脚本 ────────────────────────────────────────────────────────────
    path = SCRIPTS_DIR / (name if name.endswith(".qwf") else f"{name}.qwf")
    if not path.exists():
        report["ok"] = False
        report["error"] = f"script not found: {path}"
        return report
    text = path.read_text(encoding="utf-8")
    report["script_path"] = str(path)

    # ── 1. 解析 ──────────────────────────────────────────────────────────────
    try:
        ir = qwf.parse_qwf(text)
    except Exception as exc:  # pragma: no cover - 解析失败属于脚本自身问题
        report["ok"] = False
        report["error"] = f"parse failed: {exc}"
        return report
    stages.append(_stage("parse", True, pipeline=[s.get("op") for s in ir.get("pipeline", [])]))

    # ── 2. 校验 ──────────────────────────────────────────────────────────────
    normalized, diags = dsl_validate(ir, require_writes=False)
    errors = error_list(diags)
    stages.append(_stage("validate", not errors, errors=errors,
                         warnings=[d for d in diags if d.get("level") != "error"]))
    if errors:
        report["ok"] = False
        return report

    # ── 3. 编译成节点 config（IR → fn/qubits/coupler）────────────────────────
    env = dict(normalized.get("env") or {})
    resolve = _resolve_factory(env)
    measure_nodes: List[Tuple[str, Dict[str, Any]]] = []
    for stmt in normalized.get("pipeline", []):
        node = dsl_compile.build_node(stmt, str(stmt.get("as") or ""), resolve=resolve, env=env)
        if node and node.get("type") == "experiment":
            measure_nodes.append((str(stmt.get("as") or ""), node["config"]))
    if not measure_nodes:
        report["ok"] = False
        report["error"] = "no experiment node in pipeline"
        return report
    stages.append(_stage("compile", True, nodes=[
        {
            "as": alias,
            "fn": cfg.get("fn"),
            "qubits": cfg.get("qubits"),
            "coupler": cfg.get("coupler"),
            "params": cfg.get("params"),
        }
        for alias, cfg in measure_nodes
    ]))

    # ── 4. 节点 config → 可执行代码 ─────────────────────────────────────────
    codes: List[Tuple[str, str, Dict[str, Any]]] = []
    for alias, cfg in measure_nodes:
        fn = str(cfg.get("fn") or "")
        qubits = [str(q) for q in (cfg.get("qubits") or []) if q]
        qubit = str(cfg.get("qubit") or (qubits[0] if qubits else ""))
        params = dict(cfg.get("params") or {})
        params.setdefault("do_plot", False)
        coupler = str(cfg.get("coupler") or "").strip() or None
        try:
            code = build_experiment_code(
                fn, qubit, params,
                bool(cfg.get("reportDataset", True)),
                qubits=qubits or None,
                coupler=coupler,
            )
        except Exception as exc:
            report["ok"] = False
            report["error"] = f"build_experiment_code failed for {alias}: {exc}"
            return report
        codes.append((alias, code, cfg))
    stages.append(_stage("build_code", True, codes=[
        {"as": alias, "code_head": code.strip().splitlines()[0] if code.strip() else ""}
        for alias, code, _ in codes
    ]))

    # ── 5. 代码 → (实验名, 比特, 参数)  ← 宿主真正认的那一步 ──────────────────
    parsed: List[Dict[str, Any]] = []
    for alias, code, cfg in codes:
        exp_type, qubits, params = ExperimentSimulator.parse_experiment_code_multi(code)
        parsed.append({
            "as": alias,
            "experiment": exp_type,
            "qubits": qubits,
            "params": params,
            "coupler_in_config": cfg.get("coupler"),
            "coupler_in_qubits": (cfg.get("coupler") in (qubits or [])) if cfg.get("coupler") else False,
        })
    parse_ok = all(p["experiment"] for p in parsed)
    stages.append(_stage("parse_code", parse_ok, parsed=parsed))
    if not parse_ok:
        report["ok"] = False
        report["error"] = "could not parse experiment name out of generated code"
        return report

    # ── 6. 真跑 qiskit ───────────────────────────────────────────────────────
    results: List[Dict[str, Any]] = []
    for alias, code, cfg in codes:
        exp_type, qubits, params = ExperimentSimulator.parse_experiment_code_multi(code)
        payload: Dict[str, Any] = {
            "experiment": exp_type,
            "qubit": qubits[0] if qubits else "q0",
            "qubits": qubits,
            "params": params,
            "shots": shots,
        }
        try:
            result = qiskit_backend.run(payload)
        except Exception as exc:
            results.append({"as": alias, "experiment": exp_type, "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        row = {
            "as": alias,
            "experiment": exp_type,
            "qubits": qubits,
            "params": params,
            "ok": result.get("status") == "qiskit_completed",
            "status": result.get("status"),
            "backend": result.get("backend"),
            "metrics": result.get("metrics") or {},
            "error": result.get("error"),
            "elapsed_ms": result.get("elapsed_ms"),
        }
        results.append(row)
        if verbose:
            print(f"  [{alias}] {exp_type}: {row['status']} {row['metrics']}", file=sys.stderr)

    run_ok = all(r["ok"] for r in results) and bool(results)
    stages.append(_stage("qiskit_run", run_ok, results=results))

    report["ok"] = run_ok
    report["results"] = results
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="DSL → qiskit 全链路冒烟")
    parser.add_argument("--script", default="cz.dcz", help="qwf_scripts/experiments 下的脚本名")
    parser.add_argument("--shots", type=int, default=1000, help="每个实验的 shots")
    parser.add_argument("--list", action="store_true", help="列出可用脚本")
    parser.add_argument("--out", default="", help="把 JSON 报告写到指定文件")
    parser.add_argument("--verbose", action="store_true", help="把每个实验的结果打到 stderr")
    args = parser.parse_args(argv)

    if args.list:
        for p in sorted(SCRIPTS_DIR.glob("*.qwf")):
            print(p.stem)
        return 0

    report = run_script(args.script, args.shots, verbose=args.verbose)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"report -> {out}")
    else:
        print(text)

    if report.get("ok"):
        print(f"\n[OK] DSL->qiskit full chain passed: {args.script}")
        return 0
    print(f"\n[FAIL] chain broken: {args.script} -- {report.get('error') or 'see stages'}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

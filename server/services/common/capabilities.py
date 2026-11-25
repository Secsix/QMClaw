"""services/common/capabilities.py — 能力探测（静态 AST，不 import 硬件栈）

为什么需要它（_local/docs/ARCHITECTURE_ROADMAP.md §2.2 / §3 问题⑤）：
能力基线（"这台机器上哪些实验函数真的能调"）不能只写死在一台机器上 ——
2026-11-19 就因为基线过期，把 4 个**真实存在**的函数（spectroscopy_iq /
time_rabi / iqcenter / impa_gain）误判成"运行时不存在"。

现在的做法：
- 用 `ast` **静态解析 .py 源码**拿到"源码里有什么功能"；
- 与"运行时实际导出了什么"比对（`KNOWN_RUNTIME_EXPORTS` 基线，
  或用 `probe_runtime_exports()` 到装了 lqms 的解释器里现场探一次）；
- 输出 `missing_in_runtime`，让上层（lint / 端口层 / 目标求解）能提前知道"这个实验跑不了"。

**2026-11-19 实测结论**（D:\anaconda3\envs\lqcs，编译版 .pyd）：
sq_nodes 导出 35 个公开名字（22 个实验函数），**只缺 `pidf21`**。
双比特能力在另一个模块 `cz_nodes.DCZTuner`（类方法 + 需要耦合器），见 `TWO_QUBIT_VENDOR`。

约束：纯标准库；不 import lqms（避免 torch/MKL DLL 冲突与启动开销）；
不碰 LabRAD → 可在 CI / 影子实例离线跑。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

# 运行时实际导出（**2026-11-19 在 D:\anaconda3\envs\lqcs 实测**：
# 用 lqcs 解释器 `from lqms.measure.tuners import sq_nodes; dir(sq_nodes)`，
# 得到的 35 个公开名字。其中实验函数 22 个：
#   iqraw / iqcenter / piamp / pidf / impa_gain / t1 / ramsey_df / ramsey_t2 /
#   spinecho_t2 / xeb / time_rabi / test_timing_xyz / spectroscopy /
#   spectroscopy_auto / spectroscopy_iq / s21 / s21_dis / s21_power2d /
#   s21_zpa2d / pulse_shape / pulse_shape_ramsey / set_pi
#
# ⚠️ 2026-11-19 之前的基线把 `spectroscopy_iq` / `time_rabi` / `iqcenter` /
#    `impa_gain` 记成"运行时不存在"，那是**过期结论**（另一台机器的观测），
#    今天实测这四个都在；真正缺的只有 `pidf21`。
KNOWN_RUNTIME_EXPORTS: Dict[str, List[str]] = {
    "lqms.measure.tuners.sq_nodes": [
        "ERROR_COLOR", "INFO_COLOR", "QuantumObject", "WARN_COLOR", "basex",
        "colorama", "convertUnits", "impa_gain", "iqcenter", "iqraw", "np", "ph",
        "piamp", "pidf", "plt", "pulse_shape", "pulse_shape_ramsey", "ramsey_df",
        "ramsey_t2", "s21", "s21_dis", "s21_power2d", "s21_zpa2d", "set_pi",
        "smt_experiment", "spectroscopy", "spectroscopy_auto", "spectroscopy_iq",
        "spinecho_t2", "st", "t1", "test_timing_xyz", "time_rabi", "util", "xeb",
    ],
    "lqms.measure.tuners.base_experiment": [
        "AlphaFine", "AutoScanExperiment", "BaseExperiment", "BasicTuner",
        "Dataset", "IQraw", "MultiTypeExperiment", "PiAmpFine", "PiPulse",
        "PulseShape", "PulseShapeFreq", "PulseShapeRamsey", "Qubit",
        "Ramsey", "ResonatorPhotonDecay", "S21", "Spectroscopy",
        "SpectroscopyAuto", "SpinEchoCPMG", "T1", "TimingXYZ", "XEB",
    ],
    "lqms.measure.tuners.cz_nodes": [
        "DCZTuner", "Dataset", "DiskDict", "KeyDictCoupler", "KeyDictQubit",
        "MultiDCZTuner", "QuantumObject", "Qubit", "SMTRunner",
    ],
}

# 源码里有、运行时没有（.pyd 遮蔽导致的功能缺失）
# 2026-11-19 实测：**只剩 pidf21**。前四个是过期误判，已从名单里去掉。
KNOWN_MISSING: List[str] = ["pidf21"]

# 真机的**双比特**能力不在 sq_nodes，在 `lqms.measure.tuners.cz_nodes`：
# 它是**类方法**（不是 sq.xxx 那种模块级函数），而且要三个对象 ——
# 两个比特 + 一个耦合器（DCZTuner.__init__: qubits[:2] 是比特，qubits[2] 是 coupler）。
#
# 登记在这里，是为了让"真机到底能不能做双比特"这个问题有一个**能查的地方**；
# 但**当前 DSL/后端链调不到它**（后端拼的是 `sq.xxx(...)` 模块级函数），
# 缺口与对策见 config/experiment_configs.json 的 `vendor_capabilities.two_qubit`。
TWO_QUBIT_VENDOR: Dict[str, Dict[str, Any]] = {
    "lqms.measure.tuners.cz_nodes": {
        "class": "DCZTuner",
        "init": "DCZTuner(qubits, gate_name='DCZGate', overwrite=False)",
        "requires_coupler": True,
        "coupler_index": 2,
        "methods": [
            "initialize_gate", "swap11to20", "interaction",
            "find_zpa_c_half_cycle", "find_zpa_q", "zpa_c_m", "zpa_q_m",
            "sweep_sq_phase", "swap11to20_sq_phase", "gate_time_m",
            "zpa_q_czecho", "zpa_c_czecho", "gate_time_czecho",
            "sq_phase_xeb", "run_xeb", "tune",
        ],
        "experiment_classes": [
            "TQExperiment", "Swap11to20", "Swap11to20Entanglement",
            "Swap11to20SQphase", "CZEcho", "CZXeb", "Interaction", "CriticalZpaC",
        ],
        "batch": ["MultiDCZTuner", "SMTRunner"],
        "dsl_support": False,
        "note": "后端链目前只会拼 sq.xxx(...) 模块级函数，"
                "调不到类方法；且 DSL 的 qubits 列表描述不了耦合器。",
    },
}


def find_lqms_root() -> Optional[str]:
    """在 sys.path 中找到 lqms 包目录（不 import 它）。"""
    for entry in sys.path:
        if not entry:
            continue
        cand = os.path.join(entry, "lqms")
        if os.path.isdir(cand):
            return cand
    return None


def scan_defs(path: str) -> List[str]:
    """静态解析一个 .py 文件，返回其中定义的顶层函数与类名。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except Exception:
        return []
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return sorted(names)


def discover_shadowed(tuners_dir: str) -> List[Dict[str, Any]]:
    """找出"同目录下 .pyd 与 .py 并存"的模块（.pyd 优先被导入 → 遮蔽 .py）。"""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(tuners_dir):
        return out
    py_files = [f for f in os.listdir(tuners_dir) if f.endswith(".py")]
    for py_name in sorted(py_files):
        stem = py_name[:-3]
        pyd_candidates = [
            f for f in os.listdir(tuners_dir)
            if f.startswith(stem + ".") and f.endswith(".pyd")
        ]
        if not pyd_candidates:
            continue
        py_path = os.path.join(tuners_dir, py_name)
        pyd_path = os.path.join(tuners_dir, pyd_candidates[0])
        try:
            py_mtime = os.path.getmtime(py_path)
            pyd_mtime = os.path.getmtime(pyd_path)
        except OSError:
            py_mtime = pyd_mtime = 0.0
        out.append({
            "module": "lqms.measure.tuners." + stem,
            "source": py_path,
            "compiled": pyd_path,
            "source_mtime": py_mtime,
            "compiled_mtime": pyd_mtime,
            "source_is_newer": bool(py_mtime > pyd_mtime),
        })
    return out


def probe_runtime_exports(
    module_name: str,
    python_executable: Optional[str] = None,
    timeout: int = 30,
) -> Optional[List[str]]:
    """现场探一次运行时到底导出了什么。

    **用子进程**跑，不在本进程 import lqms —— 避免 torch/MKL DLL 冲突
    和几百 MB 的启动开销（本模块的硬约束）。

    探不到（没装 lqms / 解释器不对 / 超时）→ 返回 `None`，
    调用方回落到 `KNOWN_RUNTIME_EXPORTS` 基线。

    Args:
        module_name: 如 `lqms.measure.tuners.sq_nodes`
        python_executable: 装了 lqms 的解释器；默认当前解释器
            （服务进程的解释器通常**没有** lqms → 探不到是正常的）
        timeout: 秒
    """
    exe = python_executable or sys.executable
    code = (
        "import json,sys\n"
        "try:\n"
        f"    import {module_name} as m\n"
        "except Exception:\n"
        "    print('[]'); sys.exit(0)\n"
        "print(json.dumps(sorted(n for n in dir(m) if not n.startswith('_'))))\n"
    )
    try:
        proc = subprocess.run(
            [exe, "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None
    out = (proc.stdout or "").strip().splitlines()
    if not out:
        return None
    try:
        data = json.loads(out[-1])
    except Exception:
        return None
    return sorted(str(x) for x in data) if isinstance(data, list) else None


def capability_report(lqms_root: Optional[str] = None,
                      probe: bool = False,
                      python_executable: Optional[str] = None) -> Dict[str, Any]:
    """生成能力报告。

    Args:
        lqms_root: lqms 包目录（默认在 sys.path 里找）
        probe: 是否现场探测运行时导出（起子进程，慢一点；默认关）

    Returns:
        {
          "lqms_root": str|None,
          "shadowed_modules": [...],
          "capabilities": {module: {"source_functions":[...], "runtime_exports":[...],
                                     "missing_in_runtime":[...], "runtime_source": "probe"|"baseline"}},
          "known_missing": [...],
          "two_qubit_vendor": {...},
          "verdict": "degraded" | "ok",
          "note": "...",
        }
    """
    root = lqms_root or find_lqms_root()
    report: Dict[str, Any] = {
        "lqms_root": root,
        "shadowed_modules": [],
        "capabilities": {},
        "known_missing": list(KNOWN_MISSING),
        "two_qubit_vendor": TWO_QUBIT_VENDOR,
        "verdict": "unknown",
        "note": "",
    }
    if not root:
        report["verdict"] = "unknown"
        report["note"] = "未在 sys.path 中找到 lqms 包目录"
        return report

    tuners = os.path.join(root, "measure", "tuners")
    shadowed = discover_shadowed(tuners)
    report["shadowed_modules"] = shadowed

    for item in shadowed:
        module = item["module"]
        src_funcs = scan_defs(item["source"])
        runtime = list(KNOWN_RUNTIME_EXPORTS.get(module, []))
        source = "baseline"
        if probe:
            probed = probe_runtime_exports(module, python_executable=python_executable)
            if probed:
                runtime = probed
                source = "probe"
        missing = sorted([f for f in src_funcs if f not in runtime]) if runtime else []
        report["capabilities"][module] = {
            "source_functions": src_funcs,
            "runtime_exports": runtime,
            "missing_in_runtime": missing,
            "source_is_newer": item["source_is_newer"],
            "runtime_source": source,
        }

    sq = report["capabilities"].get("lqms.measure.tuners.sq_nodes", {})
    missing_sq = sq.get("missing_in_runtime", [])
    if missing_sq:
        report["verdict"] = "degraded"
        report["note"] = (
            "lqms 被 .pyd 遮蔽：" + ", ".join(missing_sq[:12])
            + " 在源码中存在但运行时不可用；重启服务无效，需实验室重新编译扩展。"
        )
    else:
        report["verdict"] = "ok"
        report["note"] = "未发现 .pyd 遮蔽导致的明显功能缺失"
    return report


def unavailable_experiments(report: Optional[Dict[str, Any]] = None) -> List[str]:
    """返回因能力缺失而不可用的实验函数名清单（供 lint / 前端置灰）。"""
    rep = report or capability_report()
    out: List[str] = []
    for module, info in (rep.get("capabilities") or {}).items():
        for fn in info.get("missing_in_runtime", []):
            out.append(fn)
    return sorted(set(out))

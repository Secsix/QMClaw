"""
services/quantum_service/qiskit_backend.py — qiskit 后端封装

量子服务里的**第三个后端**，和 `sq.*`（真机）、`offline_simulator`（离线合成）
并列，但**不是**仓库里另外两套同名抽象：

| 抽象 | 位置 | 和本模块的关系 |
|------|------|----------------|
| `backends.BackendInterface` | `qmclaw-server/backends/` | **无关**。那是 job_runner 用的插件协议，quantum_service 不走它 |
| `dsl.backends.BackendAdapter` | `services/workflow_service/dsl/backends/` | **无关**。那是 workflow DSL 的后端适配器，S7/S8 还没接线 |
| 本模块 | `services/quantum_service/qiskit_backend.py` | quantum_service 自己的 qiskit 入口 |

**为什么不复用 `backends/` 那套**：那套是"后端进程插件"协议（有 register /
init_backend / 任务队列 / Ray），而 qiskit 是在**服务进程内**同步执行的本地库。
硬套进来会为了一个本地库拖进一整套 Ray 任务队列，不划算。

**写闸门豁免**：真机路径的 `allow_execute` 闸门管的是"往实验室设备下发指令"。
qiskit 是纯本地模拟，**没有任何物理副作用**，所以不受闸门约束。
这一点必须显式写下来 —— 否则以后有人看到"execute 端点没查闸门"会当成漏洞。

**qiskit 是可选依赖**：本模块不 import qiskit（真正用到时才 import），
所以没装 qiskit 的机器上服务照样起得来，只是执行实验时返回明确的
"后端不可用"，而不是 HTTP 500。
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.quantum_service.qiskit_experiments import (
    BACKEND_NAME,
    UNIT_SYSTEM,
    QiskitRequest,
    QiskitUnavailable,
    coverage_report,
    get_spec,
    list_specs,
    run_experiment,
)

# --------------------------------------------------------------------- 默认值

DEFAULT_SHOTS = 2000
DEFAULT_SEED = 42
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MAX_SHOTS = 200_000
DEFAULT_MAX_POINTS = 501

# 写闸门豁免的说明（会被拼进 /qiskit/health 的响应里，别让人误读成漏检）
WRITE_GATE_EXEMPTION = (
    "qiskit 是纯本地模拟，无物理副作用，不受真机写闸门 "
    "(allow_execute / allow_set_params) 约束。"
)


def _config() -> Dict[str, Any]:
    """config/system.json 里的 qiskit 配置块。"""
    from services.common.config import load_system_config
    return (load_system_config().get("qiskit") or {})


def _version_of(dist: str) -> Optional[str]:
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return None


def _probe_interpreter(exe: Optional[str]) -> Dict[str, Any]:
    """探测某个解释器里有没有 qiskit。

    `exe` 为空表示"就用当前解释器"；否则起一个子进程去问它。
    """
    if not exe:
        try:
            import qiskit  # noqa: F401
            from qiskit_aer import AerSimulator  # noqa: F401
            return {
                "available": True,
                "error": None,
                "versions": {
                    "qiskit": getattr(qiskit, "__version__", None) or _version_of("qiskit"),
                    "qiskit_aer": _version_of("qiskit-aer"),
                },
            }
        except Exception as exc:  # noqa: BLE001 - 探测用，什么错都要能报出来
            return {"available": False, "error": f"{type(exc).__name__}: {exc}", "versions": {}}

    server_dir = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(server_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    probe = (
        "import json;"
        "import qiskit;"
        "from importlib.metadata import version;"
        "print(json.dumps({'qiskit': qiskit.__version__,"
        " 'qiskit_aer': version('qiskit-aer')}))"
    )
    try:
        proc = subprocess.run(
            [exe, "-c", probe], capture_output=True, text=True,
            encoding="utf-8", cwd=str(server_dir), env=env, timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "error": f"探测解释器失败: {type(exc).__name__}: {exc}",
            "versions": {},
        }
    if proc.returncode != 0:
        return {
            "available": False,
            "error": (proc.stderr or "").strip().splitlines()[-1] if (proc.stderr or "").strip()
            else f"探测进程退出码 {proc.returncode}",
            "versions": {},
        }
    try:
        versions = json.loads(proc.stdout)
    except Exception:
        versions = {}
    return {"available": True, "error": None, "versions": versions}


def status() -> Dict[str, Any]:
    """可用性 / 版本 / 配置。**任何情况下都不抛异常。**

    没装 qiskit 时这里返回 `available: false` + 缺失原因 + 安装指引，
    前端据此把入口置灰，而不是弹一个看不懂的 500。

    配了 `qiskit.python_executable` 的话，探的是**那个**解释器 ——
    否则会出现"子进程明明能跑，health 却说不可用"的假阴性。
    """
    cfg = _config()
    exe = str(cfg.get("python_executable") or "").strip()

    probe = _probe_interpreter(exe or None)
    available = bool(probe["available"])

    same_interpreter = False
    if exe:
        try:
            same_interpreter = (
                os.path.normcase(os.path.abspath(exe))
                == os.path.normcase(os.path.abspath(sys.executable))
            )
        except Exception:
            same_interpreter = False

    return {
        "backend": BACKEND_NAME,
        "available": available,
        "enabled": bool(cfg.get("enabled", True)),
        "unit_system": UNIT_SYSTEM,
        "versions": probe["versions"],
        "execution": "in_process" if (not exe or same_interpreter) else "subprocess",
        "python_executable": exe or sys.executable,
        "service_python": sys.executable,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "error": probe["error"],
        "install_hint": (
            "pip install -r python/requirements-qiskit.txt"
            "（或 conda activate qiskit 后 pip install qiskit qiskit-aer）"
        ),
        "write_gate": WRITE_GATE_EXEMPTION,
        "config": {
            "shots": cfg.get("shots", DEFAULT_SHOTS),
            "seed": cfg.get("seed", DEFAULT_SEED),
            "timeout_s": cfg.get("timeout_s", DEFAULT_TIMEOUT_S),
            "max_shots": cfg.get("max_shots", DEFAULT_MAX_SHOTS),
            "max_points": cfg.get("max_points", DEFAULT_MAX_POINTS),
        },
        "experiments": len(list_specs()),
    }


def list_experiments() -> Dict[str, Any]:
    """实验目录 + 覆盖情况。**不需要装 qiskit**（目录是静态声明的）。"""
    return {
        "success": True,
        "backend": BACKEND_NAME,
        "unit_system": UNIT_SYSTEM,
        "experiments": list_specs(),
        "coverage": coverage_report(),
    }


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(out, hi))


def _build_request(data: Dict[str, Any]) -> QiskitRequest:
    """把请求体转成 QiskitRequest，并做入参上限保护。

    **上限保护是同步执行的唯一有效防线**：超时只能事后发现，
    真正能挡住"shots=10^9"这种请求的是这里的钳位。
    """
    cfg = _config()
    max_shots = int(cfg.get("max_shots", DEFAULT_MAX_SHOTS))
    max_points = int(cfg.get("max_points", DEFAULT_MAX_POINTS))

    experiment = str(data.get("experiment") or data.get("exp_type") or "").strip()
    qubit = str(data.get("qubit") or data.get("name") or "").strip()

    params = data.get("params")
    if not isinstance(params, dict):
        params = {}
    params = dict(params)

    # 扫描点数也钳位 —— 13 个实验里有好几个是"每个点跑一次 Aer"，
    # points 越大耗时线性增长。
    if "points" in params:
        params["points"] = _clamp(params.get("points"), 2, max_points, 51)
    if "samples" in params:
        params["samples"] = _clamp(params.get("samples"), 2, max_points * 10, 300)

    shots = _clamp(
        data.get("shots", cfg.get("shots", DEFAULT_SHOTS)),
        1, max_shots, DEFAULT_SHOTS,
    )
    seed = _clamp(
        data.get("seed", cfg.get("seed", DEFAULT_SEED)),
        0, 2 ** 31 - 1, DEFAULT_SEED,
    )

    return QiskitRequest(
        experiment=experiment,
        qubit=qubit,
        params=params,
        shots=shots,
        seed=seed,
    )


def _plot_payload(outcome: Any, req: QiskitRequest, spec: Any) -> Dict[str, Any]:
    """转成和 `_generate_simulation_plot` 一致的绘图数据。"""
    title = f"{req.qubit or 'unknown'}: {req.experiment} (qiskit 模拟)"
    return {
        "type": outcome.plot_type,
        "x": [float(v) for v in outcome.x],
        "y": [float(v) for v in outcome.y],
        "xlabel": outcome.xlabel,
        "ylabel": outcome.ylabel,
        "title": title,
        "grid": True,
    }


def _run_circuit(data: Dict[str, Any], shots: int, seed: int) -> Dict[str, Any]:
    """跑一段 QASM2 线路（可选功能：`{"circuit": "OPENQASM 2.0; ..."}`）。

    只支持 QASM2 —— QASM3 的 importer 是另一个包（qiskit_qasm3_import），
    没有它的话 QASM3 会直接 import 失败。
    """
    from qiskit import QuantumCircuit
    from qiskit_aer import AerSimulator

    source = str(data.get("circuit") or "").strip()
    if not source:
        raise ValueError("circuit 为空")

    circ = QuantumCircuit.from_qasm_str(source)
    if circ.num_clbits == 0:
        # Aer 的 get_counts 需要有测量指令
        circ.measure_all()

    sim = AerSimulator()
    result = sim.run(circ, shots=shots, seed_simulator=seed).result()
    counts = dict(result.get_counts(0))

    # 画成各测量结果的柱状分布
    labels = sorted(counts.keys())
    total = float(sum(counts.values())) or 1.0
    return {
        "type": "bar",
        "x": list(range(len(labels))),
        "y": [counts[k] / total for k in labels],
        "labels": labels,
        "xlabel": "Outcome",
        "ylabel": "Probability",
        "title": f"{data.get('qubit') or 'unknown'}: custom circuit (qiskit 模拟)",
        "grid": True,
        "counts": counts,
        "qubits": circ.num_qubits,
    }


def run(data: Dict[str, Any]) -> Dict[str, Any]:
    """执行一次 qiskit 实验（或一段自定义线路），返回统一结果信封。

    **同步执行**，不排队、不走 Ray。Aer 是本地库，跑完就返回。

    执行位置取决于配置：
    - `config/system.json` 的 `qiskit.python_executable` **为空** → 进程内跑
      （要求 qiskit 装在服务自己的解释器里）
    - 配了且与服务解释器不同 → **子进程**跑（qiskit 装在另一个 conda 环境里）

    见 `qiskit_runner.py` 的协议说明。
    """
    if os.environ.get("QMCLAW_QISKIT_INPROCESS") == "1":
        return run_in_process(data)

    exe = str(_config().get("python_executable") or "").strip()
    if exe:
        try:
            same = os.path.normcase(os.path.abspath(exe)) == os.path.normcase(os.path.abspath(sys.executable))
        except Exception:
            same = False
        if not same:
            return _run_in_subprocess(data, exe)

    return run_in_process(data)


def _run_in_subprocess(data: Dict[str, Any], exe: str) -> Dict[str, Any]:
    """把请求转交给另一个 Python 环境里的 qiskit。"""
    started = time.time()
    server_dir = Path(__file__).resolve().parent.parent.parent
    cfg = _config()
    timeout_s = float(cfg.get("timeout_s", DEFAULT_TIMEOUT_S))

    env = dict(os.environ)
    env["PYTHONPATH"] = str(server_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["QMCLAW_QISKIT_INPROCESS"] = "1"

    # 结果走临时文件而不是 stdout：子进程启动时包的 __init__ 里有一堆
    # 会打印东西的 import（实测 "MemoryStore: Initialized ..."），
    # stdout 上多一行非 JSON，父进程就解析不了。
    fd, out_path = tempfile.mkstemp(prefix="qmclaw_qiskit_", suffix=".json")
    os.close(fd)

    try:
        proc = subprocess.run(
            [exe, "-m", "services.quantum_service.qiskit_runner", out_path],
            input=json.dumps(data or {}, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(server_dir),
            env=env,
            timeout=timeout_s + 60,   # 给进程启动/import 留余量
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "qiskit_timeout",
            "backend": BACKEND_NAME,
            "unit_system": UNIT_SYSTEM,
            "error": f"qiskit 子进程超过 {timeout_s:.0f}s 未返回",
            "stdout": "[QISKIT] 子进程超时",
            "stderr": "",
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "qiskit_error",
            "backend": BACKEND_NAME,
            "unit_system": UNIT_SYSTEM,
            "error": f"启动 qiskit 子进程失败: {type(exc).__name__}: {exc}",
            "stdout": "[QISKIT] 子进程启动失败",
            "stderr": traceback.format_exc(),
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    raw = ""
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        raw = ""
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    if proc.returncode != 0 and not raw.strip():
        return {
            "status": "qiskit_error",
            "backend": BACKEND_NAME,
            "unit_system": UNIT_SYSTEM,
            "error": f"qiskit 子进程异常退出 (code={proc.returncode})",
            "stdout": "[QISKIT] 子进程异常退出",
            "stderr": proc.stderr or "",
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    if not raw.strip():
        return {
            "status": "qiskit_error",
            "backend": BACKEND_NAME,
            "unit_system": UNIT_SYSTEM,
            "error": "qiskit 子进程没有产生结果",
            "stdout": "[QISKIT] 子进程无输出",
            "stderr": proc.stderr or "",
            "runner_stdout": (proc.stdout or "")[:2000],
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    try:
        result = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "qiskit_error",
            "backend": BACKEND_NAME,
            "unit_system": UNIT_SYSTEM,
            "error": f"解析 qiskit 子进程输出失败: {exc}",
            "stdout": "[QISKIT] 子进程输出无法解析",
            "stderr": proc.stderr or "",
            "runner_stdout": raw[:2000],
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    result.setdefault("interpreter", exe)
    if proc.stderr and proc.stderr.strip():
        result.setdefault("runner_stderr", proc.stderr[-2000:])
    return result


def run_in_process(data: Dict[str, Any]) -> Dict[str, Any]:
    """在**当前**解释器里执行一次 qiskit 实验（或一段自定义线路）。

    返回统一结果信封。**同步执行**，不排队、不走 Ray。
    """
    started = time.time()
    task_id = f"qiskit-{uuid.uuid4().hex[:12]}"
    cfg = _config()
    timeout_s = float(cfg.get("timeout_s", DEFAULT_TIMEOUT_S))

    req = _build_request(data or {})

    envelope: Dict[str, Any] = {
        "task_id": task_id,
        "status": "qiskit_completed",
        "backend": BACKEND_NAME,
        "unit_system": UNIT_SYSTEM,
        "experiment": req.experiment,
        "qubit": req.qubit,
        "stdout": "",
        "stderr": "",
        "error": None,
        "data": None,
        "metrics": {},
        "approximation": None,
        "simulated": True,
        "shots": req.shots,
        "seed": req.seed,
    }

    # 自定义线路：绕过实验目录，直接跑 QASM
    if data.get("circuit"):
        try:
            payload = _run_circuit(data, req.shots, req.seed)
        except Exception as exc:  # noqa: BLE001
            envelope["status"] = "qiskit_error"
            envelope["error"] = f"{type(exc).__name__}: {exc}"
            envelope["stderr"] = traceback.format_exc()
            envelope["stdout"] = f"[QISKIT] 自定义线路执行失败: {exc}"
            envelope["elapsed_ms"] = int((time.time() - started) * 1000)
            return envelope
        envelope["data"] = payload
        envelope["metrics"] = {"counts": payload["counts"]}
        envelope["stdout"] = (
            f"[QISKIT] 自定义线路完成: {payload['qubits']} qubits, "
            f"{req.shots} shots, {len(payload['counts'])} 种结果"
        )
        envelope["elapsed_ms"] = int((time.time() - started) * 1000)
        return envelope

    spec = get_spec(req.experiment)
    if spec is None:
        from services.quantum_service.qiskit_experiments import supported_experiments
        envelope["status"] = "qiskit_error"
        envelope["error"] = (
            f"未知实验 '{req.experiment}'。"
            f"qiskit 后端支持：{', '.join(sorted(supported_experiments()))}"
        )
        envelope["stdout"] = f"[QISKIT] {envelope['error']}"
        envelope["elapsed_ms"] = int((time.time() - started) * 1000)
        return envelope

    envelope["approximation"] = spec.approximation

    def _execute():
        return run_experiment(req)

    # 用线程包一层做"超时保护"。注意：Aer 跑在同一个进程里，
    # 超时只能让**调用方**拿到错误，后台线程还会跑到结束（Python 没法杀线程）。
    # 所以真正的防线还是 _build_request 里的入参钳位。
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qiskit-run")
    try:
        future = executor.submit(_execute)
        try:
            outcome = future.result(timeout=timeout_s)
        except FutureTimeout:
            envelope["status"] = "qiskit_timeout"
            envelope["error"] = f"执行超过 {timeout_s:.0f}s 超时"
            envelope["stdout"] = f"[QISKIT] 超时（{timeout_s:.0f}s）"
            envelope["stderr"] = traceback.format_exc()
            envelope["elapsed_ms"] = int((time.time() - started) * 1000)
            return envelope
    except QiskitUnavailable as exc:
        envelope["status"] = "qiskit_unavailable"
        envelope["error"] = str(exc)
        envelope["stdout"] = f"[QISKIT] 后端不可用: {exc}"
        envelope["elapsed_ms"] = int((time.time() - started) * 1000)
        return envelope
    except KeyError as exc:
        envelope["status"] = "qiskit_error"
        envelope["error"] = f"未知实验: {exc}"
        envelope["stdout"] = f"[QISKIT] {envelope['error']}"
        envelope["elapsed_ms"] = int((time.time() - started) * 1000)
        return envelope
    except Exception as exc:  # noqa: BLE001
        envelope["status"] = "qiskit_error"
        envelope["error"] = f"{type(exc).__name__}: {exc}"
        envelope["stdout"] = f"[QISKIT] 执行失败: {exc}"
        envelope["stderr"] = traceback.format_exc()
        envelope["elapsed_ms"] = int((time.time() - started) * 1000)
        return envelope
    finally:
        executor.shutdown(wait=False)

    envelope["data"] = _plot_payload(outcome, req, spec)
    envelope["metrics"] = outcome.metrics
    envelope["extra"] = outcome.extra
    envelope["stdout"] = (
        f"[QISKIT] {req.experiment} 完成（{spec.approximation}）: "
        f"{len(outcome.x)} 点, {req.shots} shots"
    )
    envelope["elapsed_ms"] = int((time.time() - started) * 1000)
    return envelope

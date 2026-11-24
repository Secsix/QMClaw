"""qiskit 后端单测。

分两类：

1. **不依赖 qiskit**（任何机器都能跑）
   - 实验目录、覆盖报告（14 个实验必须全覆盖）
   - 目录层与真机词表的 metrics 对齐
   - 入参钳位、未知实验、非法线路等错误路径
   - 目录层在**没装 qiskit 的解释器**里也能 import（子进程验证）

2. **依赖 qiskit**（没装就 skip，不是 fail —— qiskit 是可选依赖）
   - 14 个实验各跑一遍，断言信封结构与指标

跑法：
    python -m pytest tests/test_qiskit_backend.py -v
    python -m unittest tests.test_qiskit_backend -v

注意：跑第 2 类用 ``qiskit_backend.run`` 而不是直接调 builder ——
这样无论 qiskit 装在服务自己的解释器里（进程内）还是装在另一个 conda
环境里（子进程，config/system.json 的 qiskit.python_executable），
测试走的都是**和真实端点完全一致**的那条路。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Dict

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.quantum_service import qiskit_backend  # noqa: E402
from services.quantum_service.qiskit_experiments import (  # noqa: E402
    BACKEND_NAME,
    EXPERIMENT_REGISTRY,
    UNIT_SYSTEM,
    coverage_report,
    list_specs,
    supported_experiments,
)

# 真机词表里的 14 个实验 —— 与 config/experiment_configs.json 一致
ALL_EXPERIMENTS = [
    "t1", "ramsey", "piamp", "xeb", "single_shot", "swap", "cz.dcz",
    "spectroscopy", "pulsed_spec", "s21", "s21_dis", "iqraw",
    "allxy", "drag_calibrate",
]


def _qiskit_available() -> bool:
    try:
        return bool(qiskit_backend.status().get("available"))
    except Exception:
        return False


def _run(experiment: str, **kwargs):
    payload = {"experiment": experiment, "qubit": "q0", "shots": 400, "params": {}}
    params = kwargs.pop("params", None) or {}
    payload["params"].update(params)
    payload.update(kwargs)
    return qiskit_backend.run(payload)


class TestCatalog(unittest.TestCase):
    """目录层：不依赖 qiskit。"""

    def test_fourteen_experiments_registered(self):
        self.assertEqual(len(EXPERIMENT_REGISTRY), 14)
        self.assertEqual(sorted(supported_experiments()), sorted(ALL_EXPERIMENTS))

    def test_covers_whole_real_machine_vocabulary(self):
        """真机词表加了实验，qiskit 这边没跟上就会被这里抓住。"""
        report = coverage_report()
        self.assertEqual(report["missing_in_qiskit"], [], "qiskit 后端没覆盖全部真机实验")
        self.assertEqual(report["extra_in_qiskit"], [])
        self.assertEqual(report["covered"], report["total"])
        self.assertEqual(report["total"], 14)

    def test_list_specs_matches_real_metrics(self):
        """每个实验声明的 metrics 必须和真机词表的 metricsToExtract 同名。"""
        cfg = json.loads(
            (_SERVER_DIR / "config" / "experiment_configs.json").read_text(encoding="utf-8")
        )["experiments"]
        for spec in list_specs():
            self.assertIn(spec["name"], cfg, f"{spec['name']} 不在真机词表里")
            self.assertEqual(
                spec["metrics"],
                cfg[spec["name"]]["metricsToExtract"],
                f"{spec['name']} 的指标名与真机词表不一致",
            )
            self.assertIn(spec["approximation"], ("exact", "model"))
            self.assertTrue(spec["params"] is not None)

    def test_list_specs_works_without_qiskit(self):
        """目录是静态声明的，不该因为没装 qiskit 就列不出来。"""
        self.assertEqual(len(list_specs()), 14)
        self.assertEqual(list_specs()[0]["backend"] if "backend" in list_specs()[0] else "qiskit", "qiskit")

    def test_catalog_layer_imports_in_interpreter_without_qiskit(self):
        """拿一个没装 qiskit 的解释器导入目录层，必须成功。

        找解释器的方式：优先用 config 里配的 python_executable 的"兄弟"环境；
        找不到就跳过（不硬失败 —— 各机器环境不一样）。
        """
        candidates = []
        cfg_exe = qiskit_backend._config().get("python_executable")
        if cfg_exe:
            envs_dir = Path(cfg_exe).parent.parent   # .../envs
            for env_dir in envs_dir.glob("*"):
                py = env_dir / "python.exe"
                if py.exists() and Path(sys.executable).resolve() != py.resolve():
                    candidates.append(py)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        for exe in candidates:
            probe = subprocess.run(
                [str(exe), "-c", "import qiskit"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, env=env,
            )
            if probe.returncode == 0:
                continue  # 这个环境装了 qiskit，换下一个
            res = subprocess.run(
                [str(exe), "-c",
                 "import sys; sys.path.insert(0, r'%s');"
                 "import services.quantum_service.qiskit_experiments as m;"
                 "print(len(m.EXPERIMENT_REGISTRY))" % _SERVER_DIR],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120,
                cwd=str(_SERVER_DIR), env=env,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip().splitlines()[-1], "14")
            return
        self.skipTest("本机没找到装了 qiskit 以外解释器的 conda 环境")


class TestRequestHandling(unittest.TestCase):
    """错误路径与入参保护：不该 500，也不该拖垮服务。"""

    def test_unknown_experiment_returns_error_not_exception(self):
        res = _run("no_such_experiment")
        self.assertEqual(res["status"], "qiskit_error")
        self.assertIn("no_such_experiment", res["error"])
        self.assertTrue(res["stdout"].startswith("[QISKIT]"))

    def test_invalid_circuit_returns_error(self):
        res = qiskit_backend.run({"circuit": "这不是 QASM"})
        self.assertEqual(res["status"], "qiskit_error")
        self.assertTrue(res["error"])

    def test_shots_are_clamped(self):
        """shots=10^9 必须被钳住，否则 Aer 会一直跑。"""
        from services.quantum_service.qiskit_backend import _build_request
        max_shots = int(qiskit_backend._config().get("max_shots", 200000))
        req = _build_request({"experiment": "t1", "shots": 10 ** 9})
        self.assertEqual(req.shots, max_shots)
        req = _build_request({"experiment": "t1", "shots": -5})
        self.assertEqual(req.shots, 1)
        req = _build_request({"experiment": "t1", "shots": "abc"})
        self.assertEqual(req.shots, 2000)

    def test_points_are_clamped(self):
        from services.quantum_service.qiskit_backend import _build_request
        max_points = int(qiskit_backend._config().get("max_points", 501))
        req = _build_request({"experiment": "spectroscopy", "params": {"points": 99999}})
        self.assertEqual(req.params["points"], max_points)

    def test_health_never_raises(self):
        st = qiskit_backend.status()
        self.assertEqual(st["backend"], BACKEND_NAME)
        self.assertIn(st["execution"], ("in_process", "subprocess"))
        self.assertIn("versions", st)
        self.assertEqual(st["experiments"], 14)


@unittest.skipUnless(_qiskit_available(), "qiskit 后端不可用，跳过需要 qiskit 的用例")
class TestExperiments(unittest.TestCase):
    """14 个实验各跑一遍。

    断言的是**结构 + 量级**，不是具体数值 —— 具体数值依赖 shots/seed，
    会抖。这里只保证"指标存在、量级合理、信封完整"。
    """

    def _assert_envelope(self, res: Dict, experiment: str):  # type: ignore[name-defined]
        self.assertEqual(res["status"], "qiskit_completed", f"{experiment}: {res.get('error')}")
        self.assertEqual(res["backend"], BACKEND_NAME)
        self.assertEqual(res["unit_system"], UNIT_SYSTEM)
        self.assertTrue(res["stdout"].startswith("[QISKIT]"))
        self.assertIsNone(res["error"])
        self.assertIn(res["approximation"], ("exact", "model"))
        data = res["data"]
        self.assertIsNotNone(data, f"{experiment} 没有绘图数据")
        self.assertEqual(len(data["x"]), len(data["y"]))
        self.assertGreater(len(data["x"]), 0)
        self.assertIn(data["type"], ("line", "scatter", "bar"))
        self.assertIn("title", data)
        self.assertIn(res["qubit"] or "unknown", data["title"])
        self.assertGreater(res["elapsed_ms"], 0)

    def test_t1(self):
        res = _run("t1")
        self._assert_envelope(res, "t1")
        self.assertIn("T1", res["metrics"])
        self.assertGreater(res["metrics"]["T1"], 0)
        self.assertLess(res["metrics"]["T1"], 1e-3)

    def test_ramsey(self):
        res = _run("ramsey")
        self._assert_envelope(res, "ramsey")
        self.assertIn("T2", res["metrics"])
        self.assertGreater(res["metrics"]["T2"], 0)
        self.assertLess(res["metrics"]["T2"], 1e-3)

    def test_piamp(self):
        res = _run("piamp")
        self._assert_envelope(res, "piamp")
        self.assertIn("pi_amplitude", res["metrics"])
        self.assertGreater(res["metrics"]["pi_amplitude"], 0)
        self.assertLess(res["metrics"]["pi_amplitude"], 2.0)

    def test_xeb(self):
        res = _run("xeb", params={"cycles": 4, "sequences": 6})
        self._assert_envelope(res, "xeb")
        self.assertIn("gate_fidelity", res["metrics"])
        self.assertIn("error_per_cycle", res["metrics"])
        self.assertGreater(res["metrics"]["gate_fidelity"], 0)
        self.assertLessEqual(res["metrics"]["gate_fidelity"], 1.0)
        self.assertGreaterEqual(res["metrics"]["error_per_cycle"], 0)
        self.assertLess(res["metrics"]["error_per_cycle"], 0.5)

    def test_single_shot(self):
        res = _run("single_shot")
        self._assert_envelope(res, "single_shot")
        self.assertIn("readout_fidelity", res["metrics"])
        self.assertGreater(res["metrics"]["readout_fidelity"], 0)
        self.assertLessEqual(res["metrics"]["readout_fidelity"], 1.0)

    def test_swap(self):
        res = _run("swap")
        self._assert_envelope(res, "swap")
        self.assertIn("swap_fidelity", res["metrics"])
        self.assertGreater(res["metrics"]["swap_fidelity"], 0)
        self.assertLessEqual(res["metrics"]["swap_fidelity"], 1.0)

    def test_swap_with_explicit_two_qubits(self):
        """多比特（2026-11-19）：两个比特名要落到线路上，并出现在结果里。"""
        res = qiskit_backend.run({
            "experiment": "swap", "qubit": "q0", "qubits": ["qA", "qB"],
            "shots": 400, "params": {},
        })
        self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
        self.assertEqual(res["extra"]["qubits"], ["qA", "qB"])
        self.assertEqual(res["extra"]["qubit_a"], "qA")
        self.assertEqual(res["extra"]["qubit_b"], "qB")
        self.assertIn("qA", res["data"]["xlabel"])
        self.assertIn("qB", res["data"]["xlabel"])

    def test_cz_dcz(self):
        """双比特 CZ（DCZ）：Bell 态 + CZ 相位 + 奇偶校验。"""
        res = _run("cz.dcz")
        self._assert_envelope(res, "cz.dcz")
        self.assertIn("cz_fidelity", res["metrics"])
        self.assertIn("cz_parity", res["metrics"])
        self.assertGreater(res["metrics"]["cz_fidelity"], 0)
        self.assertLessEqual(res["metrics"]["cz_fidelity"], 1.0)
        # 正确的 CZ 把 |Φ+⟩ 翻成 |Φ−⟩ → ⟨X0X1⟩ = −1
        self.assertLess(res["metrics"]["cz_parity"], 0.5)

    def test_cz_dcz_with_explicit_two_qubits(self):
        """多比特：两个比特名要落到线路上，并出现在结果里。"""
        res = qiskit_backend.run({
            "experiment": "cz.dcz", "qubit": "q0", "qubits": ["qA", "qB"],
            "shots": 400, "params": {},
        })
        self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
        self.assertEqual(res["extra"]["qubits"], ["qA", "qB"])
        self.assertEqual(res["extra"]["qubit_a"], "qA")
        self.assertEqual(res["extra"]["qubit_b"], "qB")
        self.assertIn("qA", res["data"]["xlabel"])
        self.assertIn("qB", res["data"]["xlabel"])

    def test_drag_calibrate_reports_two_qubits(self):
        res = qiskit_backend.run({
            "experiment": "drag_calibrate", "qubit": "q0", "qubits": ["qA", "qB"],
            "shots": 400, "params": {"points": 5},
        })
        self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
        self.assertEqual(res["extra"]["qubit_compute"], "qA")
        self.assertEqual(res["extra"]["qubit_leak_flag"], "qB")

    def test_spectroscopy(self):
        res = _run("spectroscopy", params={"points": 11})
        self._assert_envelope(res, "spectroscopy")
        self.assertIn("f10", res["metrics"])
        self.assertEqual(res["approximation"], "model")

    def test_pulsed_spec(self):
        res = _run("pulsed_spec", params={"points": 11})
        self._assert_envelope(res, "pulsed_spec")
        self.assertIn("qubit_frequency", res["metrics"])

    def test_s21(self):
        res = _run("s21", params={"points": 11})
        self._assert_envelope(res, "s21")
        self.assertIn("fread", res["metrics"])

    def test_s21_dis(self):
        res = _run("s21_dis", params={"points": 11})
        self._assert_envelope(res, "s21_dis")
        self.assertIn("dispersive_shift", res["metrics"])
        self.assertGreater(res["metrics"]["dispersive_shift"], 0)

    def test_iqraw(self):
        res = _run("iqraw", params={"samples": 60})
        self._assert_envelope(res, "iqraw")
        for key in ("SNR", "F0", "F1", "separation"):
            self.assertIn(key, res["metrics"])
        self.assertGreater(res["metrics"]["SNR"], 0)
        self.assertGreater(res["metrics"]["separation"], 0)

    def test_allxy(self):
        res = _run("allxy")
        self._assert_envelope(res, "allxy")
        self.assertIn("average_fidelity", res["metrics"])
        self.assertIn("leakage", res["metrics"])
        self.assertGreater(res["metrics"]["average_fidelity"], 0)
        self.assertGreaterEqual(res["metrics"]["leakage"], 0)

    def test_drag_calibrate(self):
        res = _run("drag_calibrate", params={"points": 9})
        self._assert_envelope(res, "drag_calibrate")
        self.assertIn("optimal_drag", res["metrics"])
        self.assertGreaterEqual(res["metrics"]["optimal_drag"], -1.0)
        self.assertLessEqual(res["metrics"]["optimal_drag"], 1.0)


@unittest.skipUnless(_qiskit_available(), "qiskit 后端不可用，跳过需要 qiskit 的用例")
class TestCustomCircuit(unittest.TestCase):
    """自定义 QASM2 线路。"""

    BELL = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0],q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
"""

    def test_bell_state_counts(self):
        res = qiskit_backend.run({"circuit": self.BELL, "shots": 1000})
        self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
        counts = res["metrics"]["counts"]
        # Bell 态只出 00 / 11，且各占一半
        self.assertEqual(set(counts.keys()), {"00", "11"})
        self.assertGreater(counts.get("00", 0), 300)
        self.assertGreater(counts.get("11", 0), 300)
        self.assertEqual(res["data"]["type"], "bar")


class TestServiceRoutes(unittest.TestCase):
    """服务层路由：端点契约。"""

    @classmethod
    def setUpClass(cls):
        from services.quantum_service.server import QuantumService
        cls._service = QuantumService(port=3099)
        # 不碰 LabRAD：qiskit 端点本来就不参与 mode 语义，
        # 但把 mode 钉成 offline 可以保证这个用例绝不碰真机。
        cls._service._mode = QuantumService.Mode.OFFLINE

    @classmethod
    def tearDownClass(cls):
        try:
            cls._service.shutdown()
        except Exception:
            pass
        try:
            cls._service._writer.stop()
        except Exception:
            pass

    def test_health_route(self):
        res = self._service.handle_request("GET", "/qiskit/health", {}, {})
        self.assertTrue(res.get("success"))
        self.assertEqual(res["backend"], BACKEND_NAME)
        self.assertIn("available", res)

    def test_experiments_route(self):
        res = self._service.handle_request("GET", "/qiskit/experiments", {}, {})
        self.assertTrue(res.get("success"))
        self.assertEqual(len(res["experiments"]), 14)
        self.assertIn("coverage", res)

    def test_run_route_unknown_experiment(self):
        res = self._service.handle_request("POST", "/qiskit/run", {"experiment": "nope"}, {})
        self.assertEqual(res["status"], "qiskit_error")
        self.assertTrue(res["error"])

    @unittest.skipUnless(_qiskit_available(), "qiskit 后端不可用")
    def test_run_route_executes(self):
        res = self._service.handle_request(
            "POST", "/qiskit/run", {"experiment": "t1", "qubit": "q0", "shots": 300}, {}
        )
        self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
        self.assertIn("T1", res["metrics"])
        self.assertEqual(res["backend"], BACKEND_NAME)
        self.assertEqual(res["unit_system"], UNIT_SYSTEM)
        # 图应该画出来（前端靠 plotPath 拿图）
        self.assertTrue(res.get("plotPath", "").startswith("/plots/qiskit_sim_"))


if __name__ == "__main__":
    unittest.main()

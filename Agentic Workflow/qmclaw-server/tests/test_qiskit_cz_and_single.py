"""单比特 + CZ 门调控的 qiskit 后端验证。

计划对应：用 qiskit 后端把"单比特调控"和"CZ（双比特）调控"两条路各跑一遍，
确认下游解析层 → qiskit 执行层 → 指标输出整条链是通的。

分三类用例：

1. **解析层（不需要 qiskit）**
   - `offline_simulator.parse_experiment_code_multi` 能认出两种 CZ 调用形态：
     模块级 `cz.dcz(q0, q1)` 与真机形态 `DCZTuner([q0, q1, c0], ...).tune(...)`
     （后者由上游 `adapters.lqcs.build_experiment_code` 真实生成，耦合器按
     词表 `coupler_index` 插在比特列表第 3 位，解析时要把它摘掉 —— 耦合器
     不是比特）。
   - 真机词表里 `cz.dcz` 的 `requires_coupler` / `coupler_index` / `call` 块存在。
   - `qwf_scripts/experiments/cz.dcz.qwf` 写了 coupler 且 lint 无 error。

2. **qiskit 执行层（需要 qiskit）**
   - CZ 指标口径：理想 CZ ⇒ `cz_fidelity ≈ 1`、`bell_fidelity ≈ 1`、
     `bell_parity ≈ +1`、`cz_parity ≈ -1`（Φ+ 经 CZ 变 Φ-，X 基期望 -1）。
   - `error_rate` 变大 ⇒ `cz_fidelity` 下降（噪声确实在起作用）。

3. **单比特回归（需要 qiskit）**
   - 12 个单比特实验各跑一遍，断言信封结构与指标键。

跑法（测试机没装 qiskit 时 qiskit 是可选依赖，会 skip 而不是 fail）：
    python -m pytest tests/test_qiskit_cz_and_single.py -v

需要 qiskit 的用例走 `qiskit_backend.run` —— 与真实端点完全同一条路：
`config/system.json`（或 `config/system.local.json`）里的
`qiskit.python_executable` 若指向别的解释器，就走子进程执行。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.quantum_service import qiskit_backend  # noqa: E402
from services.quantum_service.offline_simulator import (  # noqa: E402
    ExperimentSimulator,
)

_REPO_ROOT = _SERVER_DIR.parents[1]
_SCRIPT = _REPO_ROOT / "qwf_scripts" / "experiments" / "cz.dcz.qwf"
_CONFIG_PATH = _SERVER_DIR / "config" / "experiment_configs.json"


def _qiskit_available() -> bool:
    try:
        return bool(qiskit_backend.status().get("available"))
    except Exception:
        return False


def _run(experiment: str, **kwargs):
    payload = {"experiment": experiment, "qubit": "q0", "shots": 800, "params": {}}
    params = kwargs.pop("params", None) or {}
    payload["params"].update(params)
    payload.update(kwargs)
    return qiskit_backend.run(payload)


class TestCzParsing(unittest.TestCase):
    """解析层：宿主要认得出 CZ 的两种调用形态。"""

    def test_module_level_cz_call(self):
        exp, qubits, params = ExperimentSimulator.parse_experiment_code_multi(
            "cz.dcz(q0, q1, error_rate=0.02)"
        )
        self.assertEqual(exp, "cz.dcz")
        self.assertEqual(qubits, ["q0", "q1"])
        self.assertEqual(params.get("error_rate"), 0.02)

    def test_class_call_code_roundtrip(self):
        """上游真机形态（构造 + 调方法）也要能解析出实验名与比特。"""
        from services.workflow_service.dsl.backends import lqcs

        code = lqcs.build_experiment_code(
            "cz.dcz",
            "q0",
            {"error_rate": 0.01},
            report_dataset=False,
            qubits=["q0", "q1"],
            coupler="c0",
        )
        self.assertIn("DCZTuner", code)
        self.assertIn("c0", code)

        exp, qubits, params = ExperimentSimulator.parse_experiment_code_multi(code)
        self.assertEqual(exp, "cz.dcz")
        # 耦合器不是比特：解析后只剩两个比特
        self.assertEqual(qubits, ["q0", "q1"])
        self.assertNotIn("c0", qubits)
        self.assertEqual(params.get("error_rate"), 0.01)

    def test_swap_call_not_broken(self):
        """单比特/双比特老形态不能被改坏。"""
        exp, qubits, _ = ExperimentSimulator.parse_experiment_code_multi("sq.swap(q0, q1)")
        self.assertEqual(exp, "swap")
        self.assertEqual(qubits, ["q0", "q1"])


class TestCzVocabulary(unittest.TestCase):
    """词表层：cz.dcz 的耦合器与类方法调用档案必须齐全。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))["experiments"]

    def test_cz_entry_declares_coupler(self):
        cz = self.cfg["cz.dcz"]
        self.assertEqual(cz["qubits"], 2)
        self.assertTrue(cz.get("requires_coupler"))
        self.assertEqual(cz.get("coupler_index"), 2)

    def test_cz_entry_declares_class_call(self):
        call = self.cfg["cz.dcz"].get("call") or {}
        self.assertEqual(call.get("class"), "DCZTuner")
        self.assertEqual(call.get("method"), "tune")
        self.assertIn("cz_nodes", call.get("module", ""))


class TestCzScript(unittest.TestCase):
    """脚本层：cz.dcz.qwf 必须写 coupler，且 lint 无 error。"""

    def test_script_has_coupler(self):
        text = _SCRIPT.read_text(encoding="utf-8")
        self.assertIn("coupler", text)
        self.assertIn("qubits", text)

    def test_script_lint_clean(self):
        from services.workflow_service.dsl.lint import lint_text

        report = lint_text(_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(report["errors"], [], "cz.dcz.qwf lint 有 error")


@unittest.skipUnless(_qiskit_available(), "qiskit 后端不可用，跳过需要 qiskit 的用例")
class TestCzMetrics(unittest.TestCase):
    """qiskit 执行层：CZ 指标口径。"""

    def test_ideal_cz_metrics(self):
        res = _run("cz.dcz", params={"error_rate": 0.0})
        self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
        m = res["metrics"]
        # 理想 Bell 态：|00> + |11>，奇偶 +1
        self.assertAlmostEqual(m["bell_fidelity"], 1.0, delta=0.05)
        self.assertAlmostEqual(m["bell_parity"], 1.0, delta=0.1)
        # CZ 把 |Φ+> 翻成 |Φ->，X 基期望 -1 ⇒ 保真度 (1-(-1))/2 = 1
        self.assertAlmostEqual(m["cz_parity"], -1.0, delta=0.1)
        self.assertAlmostEqual(m["cz_fidelity"], 1.0, delta=0.05)

    def test_error_rate_reduces_cz_fidelity(self):
        clean = _run("cz.dcz", params={"error_rate": 0.0})
        noisy = _run("cz.dcz", params={"error_rate": 0.08})
        self.assertEqual(clean["status"], "qiskit_completed", clean.get("error"))
        self.assertEqual(noisy["status"], "qiskit_completed", noisy.get("error"))
        self.assertGreater(clean["metrics"]["cz_fidelity"], noisy["metrics"]["cz_fidelity"])
        self.assertGreater(noisy["metrics"]["cz_fidelity"], 0.0)

    def test_two_qubit_payload_labels(self):
        res = _run("cz.dcz", qubits=["qA", "qB"], params={"error_rate": 0.0})
        self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
        self.assertEqual(res["extra"]["qubits"], ["qA", "qB"])
        self.assertEqual(res["extra"]["qubit_a"], "qA")
        self.assertEqual(res["extra"]["qubit_b"], "qB")
        # x 必须是数值，否则画图那步 float() 会炸（cz.dcz 生产路径曾因此 500）
        self.assertTrue(all(isinstance(v, (int, float)) for v in res["data"]["x"]))


@unittest.skipUnless(_qiskit_available(), "qiskit 后端不可用，跳过需要 qiskit 的用例")
class TestSingleQubitExperiments(unittest.TestCase):
    """单比特回归：12 个单比特实验各跑一遍。"""

    CASES = [
        ("t1", {}, "T1"),
        ("ramsey", {}, "T2"),
        ("piamp", {}, "pi_amplitude"),
        ("xeb", {}, "gate_fidelity"),
        ("single_shot", {}, "readout_fidelity"),
        ("spectroscopy", {"points": 11}, "f10"),
        ("pulsed_spec", {"points": 11}, "qubit_frequency"),
        ("s21", {"points": 11}, "fread"),
        ("s21_dis", {}, "dispersive_shift"),
        ("iqraw", {"samples": 60}, "SNR"),
        ("allxy", {}, "average_fidelity"),
        ("drag_calibrate", {"points": 9}, "optimal_drag"),
    ]

    def test_all_single_qubit_experiments(self):
        for experiment, params, metric in self.CASES:
            with self.subTest(experiment=experiment):
                res = _run(experiment, params=params)
                self.assertEqual(res["status"], "qiskit_completed", res.get("error"))
                self.assertIsNone(res.get("error"))
                self.assertIn(metric, res["metrics"], f"{experiment} 缺指标 {metric}")
                self.assertTrue(res["stdout"].startswith("[QISKIT]"))
                self.assertEqual(len(res["data"]["x"]), len(res["data"]["y"]))
                self.assertGreater(len(res["data"]["x"]), 0)
                self.assertIn(res["data"]["type"], ("line", "scatter", "bar"))
                self.assertGreater(res["elapsed_ms"], 0)


if __name__ == "__main__":
    unittest.main()

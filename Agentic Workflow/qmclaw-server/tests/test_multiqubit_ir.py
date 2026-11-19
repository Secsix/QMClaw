"""多比特 IR（`qubits [...]`）单测 —— 2026-11-19。

覆盖三条链路：

1. **文本 DSL → IR → 校验**：``measure { fn sq.swap  qubits ["q0","q1"] }``
2. **IR → 节点 → 后端代码**：多比特要拼成 ``sq.swap(q0, q1, ...)``，
   单比特仍然是 ``sq.t1(q2ld3, ...)``（老流程一个字节都不许变）
3. **元数校验**：比特个数与实验元数不符 → ``E_QUBIT_ARITY``（error，下发前拦住）

跑法：
    python -m pytest tests/test_multiqubit_ir.py -v
    python -m unittest tests.test_multiqubit_ir -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.workflow_service.dsl import build_node  # noqa: E402
from services.workflow_service.dsl.backends.lqcs import build_experiment_code  # noqa: E402
from services.workflow_service.dsl.experiments import qubit_arity  # noqa: E402
from services.workflow_service.dsl.textdsl import parse  # noqa: E402
from services.workflow_service.dsl.textdsl.printer import ir_to_qwf  # noqa: E402
from services.workflow_service.dsl.validate import validate  # noqa: E402
from services.quantum_service.offline_simulator import ExperimentSimulator  # noqa: E402

SWAP_QWF = """
workflow two_qubit_demo {
  env { qubit "q2ld3" }
  measure as s1 {
    fn sq.swap
    qubits ["q0", "q1"]
    timeout 300s
  }
}
"""

SINGLE_QWF = """
workflow single_qubit_demo {
  env { qubit "q2ld3" }
  measure as s1 {
    fn sq.t1
    qubit "$env.qubit"
    timeout 300s
  }
}
"""


def _codes(diags):
    return [d.get("code") for d in diags]


class TestTextDsl(unittest.TestCase):
    def test_qubits_parsed_into_ir(self):
        ir = parse(SWAP_QWF)
        self.assertEqual(ir["pipeline"][0]["qubits"], ["q0", "q1"])
        self.assertNotIn("qubit", ir["pipeline"][0])

    def test_single_qubit_still_works(self):
        """老写法必须原样可用（IR v1 纯增量）。"""
        ir = parse(SINGLE_QWF)
        self.assertEqual(ir["pipeline"][0]["qubit"], "$env.qubit")
        self.assertNotIn("qubits", ir["pipeline"][0])

    def test_round_trip_keeps_qubits(self):
        ir = parse(SWAP_QWF)
        again = parse(ir_to_qwf(ir))
        self.assertEqual(again["pipeline"][0]["qubits"], ["q0", "q1"])

    def test_arity_mismatch_is_error(self):
        """双比特实验只给 1 个比特 → 必须在下发前报错。"""
        ir = parse(SWAP_QWF.replace('qubits ["q0", "q1"]', 'qubit "q0"'))
        _norm, diags = validate(ir)
        self.assertIn("E_QUBIT_ARITY", _codes(diags))

    def test_too_many_qubits_is_error(self):
        ir = parse(SWAP_QWF.replace('fn sq.swap', 'fn sq.t1'))
        _norm, diags = validate(ir)
        self.assertIn("E_QUBIT_ARITY", _codes(diags))

    def test_matching_arity_passes(self):
        _norm, diags = validate(parse(SWAP_QWF))
        self.assertNotIn("E_QUBIT_ARITY", _codes(diags))

    def test_qubit_and_qubits_together_is_error(self):
        ir = parse(SWAP_QWF.replace('qubits ["q0", "q1"]', 'qubit "q0"\n    qubits ["q0", "q1"]'))
        _norm, diags = validate(ir)
        self.assertIn("E_SCHEMA", _codes(diags))


class TestCompile(unittest.TestCase):
    def test_node_carries_qubit_list(self):
        ir = parse(SWAP_QWF)
        node = build_node(ir["pipeline"][0], "s1")
        self.assertEqual(node["config"]["qubits"], ["q0", "q1"])
        # 兼容：qubit 仍然是第一个比特（下游按单比特读它）
        self.assertEqual(node["config"]["qubit"], "q0")

    def test_single_qubit_node_has_one_entry(self):
        ir = parse(SINGLE_QWF)
        # 编译期 resolve 是恒等；运行期才把 $env.qubit 换成具体比特名
        node = build_node(ir["pipeline"][0], "s1",
                          resolve=lambda v: "q2ld3" if v == "$env.qubit" else v)
        self.assertEqual(node["config"]["qubits"], ["q2ld3"])

    def test_code_is_multi_positional(self):
        ir = parse(SWAP_QWF)
        node = build_node(ir["pipeline"][0], "s1")
        code = build_experiment_code("sq.swap", node["config"]["qubit"], {"do_plot": False},
                                     report_dataset=True, qubits=node["config"]["qubits"])
        self.assertIn("sq.swap(q0, q1, do_plot=False)", code)

    def test_single_qubit_code_unchanged(self):
        ir = parse(SINGLE_QWF)
        node = build_node(ir["pipeline"][0], "s1",
                          resolve=lambda v: "q2ld3" if v == "$env.qubit" else v)
        code = build_experiment_code("sq.t1", node["config"]["qubit"], {"do_plot": False},
                                     report_dataset=True, qubits=node["config"]["qubits"])
        self.assertIn("sq.t1(q2ld3, do_plot=False)", code)


class TestArityTable(unittest.TestCase):
    def test_table_matches_shared_vocabulary(self):
        """词表里的 qubits 字段是唯一真源，dsl 侧必须读出同一份。"""
        cfg = json.loads(
            (_SERVER_DIR / "config" / "experiment_configs.json").read_text(encoding="utf-8")
        )["experiments"]
        for name, spec in cfg.items():
            self.assertEqual(
                qubit_arity(spec["function"]), spec["qubits"],
                f"{name} 的元数与词表不一致",
            )

    def test_two_qubit_experiments(self):
        self.assertEqual(qubit_arity("sq.swap"), 2)
        self.assertEqual(qubit_arity("sq.drag_calibrate"), 2)

    def test_unknown_experiment_is_not_blocked(self):
        """词表外的厂商函数不拦（不认识 ≠ 非法）。"""
        self.assertIsNone(qubit_arity("sq.some_vendor_thing"))


class TestQuantumSide(unittest.TestCase):
    """quantum_service 侧：代码 → (实验, 比特列表, 参数)。"""

    def test_multi_qubit_code(self):
        exp, qubits, params = ExperimentSimulator.parse_experiment_code_multi(
            "sq.swap(q0, q1, do_plot=False)")
        self.assertEqual(exp, "swap")
        self.assertEqual(qubits, ["q0", "q1"])
        self.assertEqual(params, {"do_plot": False})

    def test_single_qubit_code_unchanged(self):
        exp, qubits, params = ExperimentSimulator.parse_experiment_code_multi(
            "sq.t1(q2ld3, points=21, zpa=0.0, delay=range(0, 2000, 100))")
        self.assertEqual(exp, "t1")
        self.assertEqual(qubits, ["q2ld3"])
        self.assertEqual(params["points"], 21)
        self.assertEqual(params["zpa"], 0.0)
        self.assertEqual(params["delay"], "range(0, 2000, 100)")

    def test_legacy_signature_still_returns_first_qubit(self):
        exp, qubit, params = ExperimentSimulator.parse_experiment_code(
            "sq.swap(q0, q1, do_plot=False)")
        self.assertEqual(exp, "swap")
        self.assertEqual(qubit, "q0")
        self.assertEqual(params, {"do_plot": False})


class TestVendorBaseline(unittest.TestCase):
    """厂商能力基线（2026-11-19 实测校正）的一致性回归。

    背景：能力基线曾经过期，把 4 个**真实存在**的函数（spectroscopy_iq /
    time_rabi / iqcenter / impa_gain）误判成"运行时不存在"，
    还把一个真正不存在的 `sq.pidf21` 留在 lint 白名单里。
    """

    def test_lint_whitelist_matches_runtime_exports(self):
        """lint 放行的函数，运行时必须真的导得出。"""
        from services.common.capabilities import KNOWN_RUNTIME_EXPORTS
        from services.workflow_service.dsl.lint import SQ_NODE_FNS

        runtime = set(KNOWN_RUNTIME_EXPORTS["lqms.measure.tuners.sq_nodes"])
        for fn in SQ_NODE_FNS:
            bare = fn.split(".", 1)[-1]
            self.assertIn(bare, runtime, f"{fn} 在运行时基线里不存在")

    def test_vendor_only_fns_are_not_in_whitelist(self):
        """厂商源码有、运行时没有的函数（如 pidf21）不能进白名单，只能单独列。"""
        from services.workflow_service.dsl.lint import (
            SQ_NODE_FNS,
            SQ_NODE_FNS_VENDOR_ONLY,
        )

        self.assertNotIn("sq.pidf21", SQ_NODE_FNS)
        self.assertIn("sq.pidf21", SQ_NODE_FNS_VENDOR_ONLY)

    def test_known_missing_is_only_pidf21(self):
        from services.common.capabilities import KNOWN_MISSING

        self.assertEqual(KNOWN_MISSING, ["pidf21"])

    def test_four_experiments_exist_at_runtime(self):
        """这四个今天实测都在，别再当成缺失。"""
        from services.common.capabilities import KNOWN_RUNTIME_EXPORTS

        runtime = set(KNOWN_RUNTIME_EXPORTS["lqms.measure.tuners.sq_nodes"])
        for fn in ("spectroscopy_iq", "time_rabi", "iqcenter", "impa_gain"):
            self.assertIn(fn, runtime)


class TestTwoQubitVendor(unittest.TestCase):
    """真机双比特能力登记（cz_nodes.DCZTuner）。"""

    def test_vendor_block_registered(self):
        from services.workflow_service.dsl.experiments import two_qubit_vendor

        block = two_qubit_vendor()
        self.assertEqual(block.get("module"), "lqms.measure.tuners.cz_nodes")
        self.assertEqual(block.get("class"), "DCZTuner")
        self.assertTrue(block.get("requires_coupler"))
        self.assertIn("swap11to20", block.get("methods") or [])

    def test_swap_note_points_to_cz_nodes(self):
        """词表里 swap 的说明要指向真正的真机路径，而不是"真机没有"。"""
        cfg = json.loads(
            (_SERVER_DIR / "config" / "experiment_configs.json").read_text(encoding="utf-8")
        )
        swap = cfg["experiments"]["swap"]
        self.assertFalse(swap.get("available", True))
        self.assertIn("cz_nodes", swap["note"])
        self.assertTrue("coupler" in swap["note"].lower() or "耦合器" in swap["note"])


if __name__ == "__main__":
    unittest.main()

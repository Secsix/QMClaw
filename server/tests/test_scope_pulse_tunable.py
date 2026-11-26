"""pulse / tunable 两个新 scope 与配套判据的测试（状态图配置化 P3）。

覆盖三件事：
1. 结构：四个 scope 都能出表、边数与证据契约对得上；
2. 判据：`drag_delta` / `single_qubit_fidelity` / `zpa_curve` / `t1_by_zpa` 的求值；
3. 推进：声明式 `when` 能把证据推成 `drag_tuned` / `fidelity_verified` /
   `zpa_mapped` / `t1_mapped`，且能力档案能按标签把 scope 关掉。
"""

from __future__ import annotations

import unittest

from services.common import machine_profile as mp
from services.common import state_expr, state_machine as sm
from services.common.metrics import evaluate, load_criteria


MACHINE = sm.load_state_machine()
CRITERIA = load_criteria()


def _derive(scope, evidence):
    view = sm.machine_for_scope(scope, MACHINE)
    return state_expr.derive_state_declarative(
        view["states"], evidence, CRITERIA, scope=scope, require_when=True
    )


class TestScopeStructure(unittest.TestCase):
    def test_four_scopes_declared(self):
        self.assertEqual(list(sm.scope_specs(MACHINE)), ["qubit", "pair", "pulse", "tunable"])

    def test_qubit_has_both_orders_and_t2_and_drag(self):
        edges = sm.machine_for_scope("qubit", MACHINE)["edges"]
        ids = [e["id"] for e in edges]
        self.assertIn("freq_locked->state_discriminated", ids)  # 先区分再标幅
        self.assertIn("freq_locked->amp_calibrated", ids)  # 先标幅再区分
        self.assertIn("state_discriminated--self--t2", ids)
        self.assertIn("state_discriminated--self--drag", ids)
        self.assertIn("amp_calibrated--self--drag", ids)
        self.assertEqual(len(edges), 15)

    def test_pulse_table(self):
        table = sm.state_table_extended(MACHINE, scope="pulse")
        self.assertEqual(list(table["states"]), ["pulse_unknown", "drag_tuned", "fidelity_verified"])
        self.assertEqual(len(table["edges"]), 3)
        self.assertIn("drag_delta", table["evidence_sources"])
        self.assertIn("single_qubit_fidelity", table["evidence_sources"])

    def test_tunable_table(self):
        table = sm.state_table_extended(MACHINE, scope="tunable")
        self.assertEqual(list(table["states"]), ["tunable_unknown", "zpa_mapped", "t1_mapped"])
        self.assertEqual(len(table["edges"]), 4)
        self.assertIn("zpa_curve", table["evidence_sources"])
        self.assertIn("t1_by_zpa", table["evidence_sources"])

    def test_every_scope_edge_action_is_registered(self):
        semantics = (MACHINE.get("action_semantics") or {}).get("fns") or {}
        missing = []
        for scope in ("qubit", "pair", "pulse", "tunable"):
            for edge in sm.machine_for_scope(scope, MACHINE)["edges"]:
                fn = str((edge.get("action") or {}).get("fn") or "").strip()
                if fn and fn not in semantics:
                    missing.append((scope, edge.get("id"), fn))
        self.assertEqual(missing, [], "这些动作没有登记写语义：%r" % (missing,))


class TestNewCriteria(unittest.TestCase):
    def test_drag_delta(self):
        self.assertEqual(evaluate("drag_delta", 0.02, CRITERIA)["verdict"], "tuned")
        self.assertEqual(evaluate("drag_delta", -0.30, CRITERIA)["verdict"], "off_target")
        self.assertFalse(evaluate("drag_delta", 0.30, CRITERIA)["ok"])

    def test_single_qubit_fidelity(self):
        self.assertEqual(evaluate("single_qubit_fidelity", 0.9995, CRITERIA)["verdict"], "verified")
        self.assertEqual(evaluate("single_qubit_fidelity", 0.97, CRITERIA)["verdict"], "degraded")

    def test_curve_points_accepts_dict_and_scalar(self):
        self.assertEqual(evaluate("zpa_curve", 6, CRITERIA)["verdict"], "mapped")
        self.assertEqual(evaluate("zpa_curve", {"points": 3}, CRITERIA)["verdict"], "insufficient")
        self.assertEqual(evaluate("t1_by_zpa", 5, CRITERIA)["verdict"], "mapped")
        self.assertEqual(evaluate("t1_by_zpa", 4, CRITERIA)["verdict"], "insufficient")


class TestDeclarativeProgression(unittest.TestCase):
    def test_pulse_states(self):
        self.assertEqual(_derive("pulse", {})["state"], "pulse_unknown")
        self.assertEqual(_derive("pulse", {"drag_delta": 0.02})["state"], "drag_tuned")
        self.assertEqual(_derive("pulse", {"drag_delta": 0.30})["state"], "pulse_unknown")
        both = {"drag_delta": 0.02, "single_qubit_fidelity": 0.9995}
        self.assertEqual(_derive("pulse", both)["state"], "fidelity_verified")
        # 只给保真度不给 DRAG：不能跳过 drag_tuned
        self.assertEqual(_derive("pulse", {"single_qubit_fidelity": 0.9995})["state"], "pulse_unknown")

    def test_pulse_drag_flag(self):
        self.assertEqual(_derive("pulse", {"drag_tuned": True})["state"], "drag_tuned")

    def test_tunable_states(self):
        self.assertEqual(_derive("tunable", {})["state"], "tunable_unknown")
        self.assertEqual(_derive("tunable", {"zpa_curve": 6})["state"], "zpa_mapped")
        self.assertEqual(_derive("tunable", {"zpa_curve": 2})["state"], "tunable_unknown")
        self.assertEqual(_derive("tunable", {"zpa_curve": 6, "t1_by_zpa": 5})["state"], "t1_mapped")
        self.assertEqual(_derive("tunable", {"t1_by_zpa": 5})["state"], "tunable_unknown")

    def test_state_expr_maps_new_evidence_keys(self):
        for key in ("drag_delta", "single_qubit_fidelity", "zpa_curve", "t1_by_zpa"):
            self.assertEqual(state_expr.EVIDENCE_METRICS[key], key)
            self.assertEqual(state_expr.METRIC_INPUTS[key], (key,))


class TestCapabilityGating(unittest.TestCase):
    def test_absent_tags_disable_optional_scopes(self):
        caps = {"capabilities": {}}
        self.assertFalse(mp.scope_status("pulse", MACHINE, caps)["enabled"])
        self.assertFalse(mp.scope_status("tunable", MACHINE, caps)["enabled"])
        self.assertTrue(mp.scope_status("qubit", MACHINE, caps)["enabled"])

    def test_tags_enable_scopes(self):
        caps = {"capabilities": {"drag_required": True, "tunable": True}}
        self.assertTrue(mp.scope_status("pulse", MACHINE, caps)["enabled"])
        self.assertTrue(mp.scope_status("tunable", MACHINE, caps)["enabled"])

    def test_disabled_scope_cannot_be_resolved(self):
        caps = {"capabilities": {}}
        with self.assertRaises(ValueError):
            mp.resolve_machine("pulse", MACHINE, caps)

    def test_enabled_scopes_list(self):
        # enabled_scopes 返回的是全量清单（每行带 enabled），不是筛过的名单
        caps = {"capabilities": {"coupler": True}}
        rows = mp.enabled_scopes(MACHINE, caps)
        self.assertEqual([row["scope"] for row in rows], ["qubit", "pair", "pulse", "tunable"])
        self.assertEqual([row["scope"] for row in rows if row["enabled"]], ["qubit", "pair"])


if __name__ == "__main__":
    unittest.main()

"""芯片能力档案（machine_profile）单测。

覆盖三件事：
1. scope 声明：有 ``scopes`` 段按声明取表，没有则回落到 qubit + pair 旧键；
2. 能力过滤：没有能力档案时全部启用（老部署行为不变），有档案时按标签/人工覆盖裁；
3. 覆盖与解释：``edge_overrides`` 能禁边/改参数，``explain`` 能说清每条边的去留。
"""

import copy
import json
import os
import tempfile
import unittest

from services.common import machine_profile as mp
from services.common import state_machine as sm


def _machine_with_scopes():
    """最小结构表：两个 scope，外加一个能力驱动的第三个 scope。"""
    return {
        "version": "9.9.9",
        "states": {
            "a": {"index": 0, "description": "起点", "ttl_s": None},
            "b": {"index": 1, "description": "终点", "ttl_s": 60},
        },
        "edges": [
            {"id": "a->b", "kind": "forward", "from": "a", "to": "b", "action": {"fn": "sq.x"}},
        ],
        "pair_states": {"p0": {"index": 0, "ttl_s": None}, "p1": {"index": 1, "ttl_s": 60}},
        "pair_edges": [
            {"id": "p0->p1", "kind": "forward", "from": "p0", "to": "p1", "action": {"fn": "cz.y"}},
        ],
        "gates": {},
        "scopes": {
            "qubit": {"states_key": "states", "edges_key": "edges", "enabled_by": []},
            "pair": {"states_key": "pair_states", "edges_key": "pair_edges", "enabled_by": ["coupler"]},
            "pulse": {
                "states_key": "states",
                "edges_key": "edges",
                "enabled_by": ["drag_required"],
                "label": "脉冲质量",
            },
        },
    }


def _write_caps(directory, payload):
    path = os.path.join(directory, "device_capabilities.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return path


class TestScopeDeclarations(unittest.TestCase):
    def test_legacy_machine_falls_back_to_qubit_pair(self):
        machine = {
            "states": {"a": {"index": 0}},
            "edges": [{"id": "a->a", "from": "a", "to": "a"}],
            "pair_states": {"p": {"index": 0}},
            "pair_edges": [{"id": "p->p", "from": "p", "to": "p"}],
        }
        specs = sm.scope_specs(machine)
        self.assertEqual(list(specs), ["qubit", "pair"])
        self.assertEqual(specs["pair"]["states_key"], "pair_states")
        self.assertEqual(specs["pair"]["engine"], "legacy")

    def test_declared_scopes_are_used_and_ordered(self):
        specs = sm.scope_specs(_machine_with_scopes())
        self.assertEqual(list(specs), ["qubit", "pair", "pulse"])
        self.assertEqual(specs["pulse"]["label"], "脉冲质量")
        # 未声明的键按 scope 名推导
        machine = _machine_with_scopes()
        machine["scopes"]["future"] = {"enabled_by": []}
        self.assertEqual(sm.scope_specs(machine)["future"]["states_key"], "future_states")

    def test_machine_for_scope_returns_declared_tables(self):
        view = sm.machine_for_scope("pair", _machine_with_scopes())
        self.assertEqual(sorted(view["states"]), ["p0", "p1"])
        self.assertEqual([e["id"] for e in view["edges"]], ["p0->p1"])

    def test_unknown_scope_lists_available(self):
        with self.assertRaises(ValueError) as ctx:
            sm.machine_for_scope("nope", _machine_with_scopes())
        self.assertIn("qubit", str(ctx.exception))

    def test_scope_without_tables_raises(self):
        machine = _machine_with_scopes()
        machine["scopes"]["pulse"]["states_key"] = "pulse_states"
        with self.assertRaises(ValueError):
            sm.machine_for_scope("pulse", machine)


class TestCapabilityFiltering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_no_profile_enables_everything(self):
        machine = _machine_with_scopes()
        status = mp.scope_status("pair", machine, {})
        self.assertTrue(status["enabled"])
        self.assertEqual(status["source"], "structure")
        rows = mp.enabled_scopes(machine, {})
        self.assertTrue(all(row["enabled"] for row in rows))

    def test_missing_tag_disables_scope(self):
        machine = _machine_with_scopes()
        caps = {"capabilities": {"drag_required": True}}
        self.assertFalse(mp.scope_status("pair", machine, caps)["enabled"])
        self.assertTrue(mp.scope_status("qubit", machine, caps)["enabled"])
        self.assertIn("coupler", mp.scope_status("pair", machine, caps)["reason"])

    def test_override_wins_over_capabilities(self):
        machine = _machine_with_scopes()
        caps = {"capabilities": {}, "scopes": {"pulse": {"enabled": True}}}
        status = mp.scope_status("pulse", machine, caps)
        self.assertTrue(status["enabled"])
        self.assertEqual(status["source"], "override")

        caps = {"capabilities": {"coupler": True}, "scopes": {"pair": {"enabled": False}}}
        self.assertFalse(mp.scope_status("pair", machine, caps)["enabled"])

    def test_resolve_disabled_scope_raises(self):
        machine = _machine_with_scopes()
        with self.assertRaises(ValueError) as ctx:
            mp.resolve_machine("pair", machine, {"capabilities": {}})
        self.assertIn("未启用", str(ctx.exception))

    def test_capabilities_cache_invalidates_on_change(self):
        path = _write_caps(self.tmp.name, {"capabilities": {"coupler": False}})
        first, _ = mp.load_capabilities(path)
        self.assertFalse(mp.capability_tags(first)["coupler"])
        _write_caps(self.tmp.name, {"capabilities": {"coupler": True}})
        os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))
        again, _ = mp.load_capabilities(path)
        self.assertTrue(mp.capability_tags(again)["coupler"])


class TestEdgeOverrides(unittest.TestCase):
    def test_disabled_edge_disappears(self):
        machine = _machine_with_scopes()
        caps = {"capabilities": {"coupler": True}, "edge_overrides": {"p0->p1": {"enabled": False}}}
        view = mp.resolve_machine("pair", machine, caps)
        self.assertEqual(view["edges"], [])

    def test_patch_keeps_other_fields(self):
        machine = _machine_with_scopes()
        caps = {"capabilities": {"coupler": True}, "edge_overrides": {"p0->p1": {"max_attempts": 1}}}
        view = mp.resolve_machine("pair", machine, caps)
        self.assertEqual(len(view["edges"]), 1)
        self.assertEqual(view["edges"][0]["max_attempts"], 1)
        self.assertEqual(view["edges"][0]["action"]["fn"], "cz.y")

    def test_profile_machine_does_not_mutate_base(self):
        machine = _machine_with_scopes()
        before = copy.deepcopy(machine)
        mp.profile_machine(machine, {"capabilities": {"coupler": True}, "edge_overrides": {"p0->p1": {"enabled": False}}})
        self.assertEqual(machine, before)

    def test_qubit_edges_unaffected_by_pair_override(self):
        machine = _machine_with_scopes()
        caps = {"edge_overrides": {"p0->p1": {"enabled": False}}}
        view = mp.resolve_machine("qubit", machine, caps)
        self.assertEqual([e["id"] for e in view["edges"]], ["a->b"])


class TestExplainAndSummarize(unittest.TestCase):
    def test_explain_reports_edges_and_reason(self):
        machine = _machine_with_scopes()
        caps = {"capabilities": {"drag_required": True}, "edge_overrides": {"a->b": {"max_attempts": 2}}}
        info = mp.explain("pulse", machine, caps)
        self.assertTrue(info["enabled"])
        self.assertEqual([e["id"] for e in info["edges"]], ["a->b"])
        self.assertTrue(info["edges"][0]["overridden"])

    def test_explain_disabled_scope_has_no_edges(self):
        machine = _machine_with_scopes()
        info = mp.explain("pair", machine, {"capabilities": {}})
        self.assertFalse(info["enabled"])
        self.assertEqual(info["edges"], [])

    def test_summarize_shape(self):
        machine = _machine_with_scopes()
        data = mp.summarize(machine, {})
        self.assertTrue(data["success"])
        self.assertEqual(data["structure_version"], "9.9.9")
        self.assertEqual([row["scope"] for row in data["scopes"]], ["qubit", "pair", "pulse"])
        self.assertTrue(all(row["enabled"] for row in data["scopes"]))
        self.assertIn("没有 device_capabilities", data["capabilities_source"])

    def test_summarize_with_profile_marks_source(self):
        machine = _machine_with_scopes()
        caps = {"chip": "T", "capabilities": {"coupler": True}}
        data = mp.summarize(machine, caps)
        self.assertEqual(data["chip"], "T")
        self.assertEqual(data["capabilities_source"], "config/device_capabilities.json")
        pair = [row for row in data["scopes"] if row["scope"] == "pair"][0]
        self.assertTrue(pair["enabled"])
        self.assertEqual(pair["edge_count"], 1)


class TestRealStructure(unittest.TestCase):
    """真实结构表：没有能力档案时必须与 v1.2.0 行为一致。"""

    def test_real_machine_scopes(self):
        specs = sm.scope_specs()
        self.assertEqual(list(specs)[:2], ["qubit", "pair"])
        self.assertEqual(sm.scope_states("qubit"), [
            "unknown", "freq_locked", "amp_calibrated",
            "state_discriminated", "coherence_measured", "characterized",
        ])
        self.assertEqual(len(sm.all_edges(sm.machine_for_scope("pair"))), 13)

    def test_real_table_still_six_states(self):
        table = sm.state_table_extended()
        self.assertEqual(len(table["states"]), 6)
        self.assertEqual(len(table["edges"]), 11)
        self.assertEqual(table["scope"], "qubit")


if __name__ == "__main__":
    unittest.main()

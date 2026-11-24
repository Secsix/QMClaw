"""状态机结构层（`config/state_machine.json` + `services/common/state_machine.py`）单测 —— 2026-11-24。

覆盖 P0（BACKLOG O1）的四件事：

1. **结构配置可加载**：六态齐全、TTL 分档、11 条边（5 前向 / 3 自环 / 3 回退）；
2. **合法边 A(s)**：每个态能取到的边符合设计稿 §4.2 的边表；
3. **状态结论记录**：状态 + 凭据 + 判据版本 + 时效（设计稿 §5.2）；
4. **回归**：`qubit_state.derive_state()` 的判定口径**一个字节都没变**（红线）。

跑法：
    python -m pytest tests/test_state_machine.py -v
    python -m unittest tests.test_state_machine -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.common.metrics import load_criteria  # noqa: E402
from services.common.qubit_state import derive_state, state_table  # noqa: E402
from services.common.state_machine import (  # noqa: E402
    all_edges,
    build_state_record,
    edge_by_id,
    edges_for,
    gate_config,
    load_state_machine,
    record_validity,
    state_definitions,
    state_table_extended,
    state_version,
    templates,
    ttl_for,
)


class TestStateMachineConfig(unittest.TestCase):
    """结构配置本身。"""

    def test_config_loads(self):
        machine = load_state_machine()
        self.assertTrue(machine.get("version"))
        self.assertEqual(machine.get("criteria_source"), "config/lab_criteria.json")

    def test_state_version_is_string(self):
        self.assertIsInstance(state_version(), str)
        self.assertTrue(state_version())

    def test_six_states_with_ttl(self):
        states = state_definitions()
        self.assertEqual(
            list(states.keys()),
            [
                "unknown",
                "freq_locked",
                "amp_calibrated",
                "state_discriminated",
                "coherence_measured",
                "characterized",
            ],
        )
        for index, name in enumerate(states):
            self.assertEqual(states[name].get("index"), index)
            self.assertIn("description", states[name])

    def test_ttl_buckets(self):
        self.assertIsNone(ttl_for("unknown"))          # 永不过期
        self.assertEqual(ttl_for("freq_locked"), 86400)      # 1 天
        self.assertEqual(ttl_for("amp_calibrated"), 259200)   # 3 天
        self.assertEqual(ttl_for("state_discriminated"), 259200)
        self.assertEqual(ttl_for("coherence_measured"), 1209600)  # 14 天
        self.assertEqual(ttl_for("characterized"), 2592000)       # 30 天

    def test_gates_config(self):
        gates = gate_config()
        self.assertEqual(set(gates.keys()), {"PRE", "VER", "TTL"})
        self.assertEqual(gates["VER"].get("on_version_mismatch"), "block")
        self.assertEqual(gates["PRE"].get("on_violation"), "block")
        self.assertEqual(gates["TTL"].get("on_violation"), "block")

    def test_templates_empty_in_p0(self):
        self.assertEqual(templates(), [])


class TestEdgeTable(unittest.TestCase):
    """边表 A(s)。"""

    def test_edge_count_and_kinds(self):
        edges = all_edges()
        self.assertEqual(len(edges), 11)
        kinds = [e.get("kind") for e in edges]
        self.assertEqual(kinds.count("forward"), 5)
        self.assertEqual(kinds.count("self"), 3)
        self.assertEqual(kinds.count("back"), 3)

    def test_edge_ids_unique(self):
        ids = [e.get("id") for e in all_edges()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_edge_has_required_fields(self):
        for edge in all_edges():
            self.assertIn(edge.get("kind"), ("forward", "self", "back"), edge.get("id"))
            self.assertIn(edge.get("from"), state_definitions(), edge.get("id"))
            self.assertIn(edge.get("to"), state_definitions(), edge.get("id"))
            self.assertTrue(edge.get("requires"), edge.get("id"))
            self.assertTrue((edge.get("action") or {}).get("fn"), edge.get("id"))

    def test_self_edges_have_max_attempts(self):
        for edge in all_edges():
            if edge.get("kind") == "self":
                self.assertGreaterEqual(edge.get("max_attempts") or 0, 1, edge.get("id"))

    def test_back_edges_have_trigger(self):
        for edge in all_edges():
            if edge.get("kind") == "back":
                self.assertTrue(edge.get("trigger"), edge.get("id"))

    def test_a_of_each_state(self):
        expected = {
            "unknown": 1,
            "freq_locked": 2,
            "amp_calibrated": 3,
            "state_discriminated": 3,
            "coherence_measured": 2,
            "characterized": 0,
        }
        for state, count in expected.items():
            self.assertEqual(len(edges_for(state)), count, state)

    def test_a_of_amp_calibrated_contains_back_edge(self):
        ids = [e.get("id") for e in edges_for("amp_calibrated")]
        self.assertIn("amp_calibrated->state_discriminated", ids)
        self.assertIn("amp_calibrated--self--resweep", ids)
        self.assertIn("amp_calibrated->freq_locked", ids)

    def test_edge_by_id(self):
        edge = edge_by_id("freq_locked->amp_calibrated")
        self.assertIsNotNone(edge)
        self.assertEqual(edge.get("from"), "freq_locked")
        self.assertEqual(edge.get("to"), "amp_calibrated")
        self.assertIsNone(edge_by_id("no-such-edge"))


class TestStateRecord(unittest.TestCase):
    """状态结论记录（设计稿 §5.2）。"""

    def _record(self, state="freq_locked", now=1000.0):
        derived = derive_state({"freq_locked": True})
        self.assertEqual(derived["state"], state)
        return build_state_record(
            qubit="q0",
            state_result=derived,
            evidence={"freq_locked": True},
            sources={
                "freq_locked": {
                    "run_id": "run-1",
                    "dataset_ids": ["ds-1"],
                    "functions": ["spectroscopy"],
                    "finished_at": 900.0,
                    "status": "success",
                }
            },
            conflicts=[],
            criteria_version="1.0.0",
            now=now,
        )

    def test_record_shape(self):
        record = self._record()
        self.assertEqual(record["qubit"], "q0")
        self.assertEqual(record["scope"]["type"], "qubit")
        self.assertEqual(record["scope"]["qubits"], ["q0"])
        self.assertEqual(record["state"], "freq_locked")
        self.assertEqual(record["state_index"], 1)
        self.assertEqual(record["state_version"], state_version())
        self.assertEqual(record["criteria_version"], "1.0.0")
        self.assertEqual(record["decided_at"], 1000.0)
        self.assertEqual(record["expires_at"], 1000.0 + 86400)

    def test_record_evidence_provenance(self):
        record = self._record()
        item = record["evidence"]["freq_locked"]
        self.assertEqual(item["run_id"], "run-1")
        self.assertEqual(item["dataset_ids"], ["ds-1"])
        self.assertEqual(item["measured_at"], 900.0)
        self.assertEqual(item["source"], "sq.spectroscopy")

    def test_unknown_state_never_expires(self):
        derived = derive_state({})
        self.assertEqual(derived["state"], "unknown")
        record = build_state_record(qubit="q1", state_result=derived, now=1000.0)
        self.assertIsNone(record["expires_at"])

    def test_record_validity(self):
        record = self._record()
        self.assertTrue(record_validity(record, now=1000.0)["valid"])
        expired = record_validity(record, now=1000.0 + 86401)
        self.assertFalse(expired["valid"])
        self.assertTrue(expired["expired"])


class TestStateTableExtended(unittest.TestCase):
    """状态表扩展：旧键一字不改，新键只增。"""

    def test_old_keys_preserved(self):
        old = state_table()
        new = state_table_extended()
        for key, value in old.items():
            self.assertIn(key, new)
            self.assertEqual(new[key], value, key)

    def test_new_keys_present(self):
        table = state_table_extended()
        self.assertEqual(len(table["edges"]), 11)
        self.assertIn("gates", table)
        self.assertEqual(table["state_version"], state_version())
        self.assertEqual(table["state_ttl"]["freq_locked"], 86400)


class TestDeriveStateRegression(unittest.TestCase):
    """红线：`qubit_state.derive_state()` 的判定口径没被这次改动影响。"""

    def test_unknown(self):
        self.assertEqual(derive_state({})["state"], "unknown")

    def test_freq_locked(self):
        self.assertEqual(derive_state({"freq_locked": True})["state"], "freq_locked")

    def test_contrast_promotes_to_state_discriminated(self):
        criteria = load_criteria()
        state = derive_state({"freq_locked": True, "contrast": 0.75}, criteria)
        self.assertEqual(state["state"], "state_discriminated")

    def test_criteria_version_reported(self):
        criteria = load_criteria()
        state = derive_state({"freq_locked": True}, criteria)
        self.assertEqual(state["criteria_version"], criteria.get("version"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# P1（BACKLOG O2）：三道闸门求值
# ---------------------------------------------------------------------------

import copy  # noqa: E402

from services.common.state_machine import (  # noqa: E402
    action_matches_edge,
    evaluate_gates,
    resolve_edges,
)


class TestGateEvaluation(unittest.TestCase):
    """GATE-PRE / GATE-VER / GATE-TTL：纯计算、只读、不触 IO。"""

    NOW = 1000.0

    def _record(
        self,
        state="unknown",
        criteria_version="1.0.0",
        expires_in=3600.0,
        evidence=None,
    ):
        return {
            "qubit": "q0",
            "scope": {"type": "qubit", "qubits": ["q0"], "coupler": None},
            "state": state,
            "state_index": 0,
            "state_version": "1.0.0",
            "criteria_version": criteria_version,
            "evidence": evidence or {},
            "conflicts": [],
            "reasons": [],
            "missing": [],
            "decided_at": self.NOW,
            "expires_at": (self.NOW + expires_in) if expires_in is not None else None,
        }

    # ---- GATE-PRE ----------------------------------------------------
    def test_pre_blocks_when_state_not_ready(self):
        result = evaluate_gates(
            self._record(state="unknown"),
            {"fn": "sq.piamp"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "block")
        self.assertIn("PRE", result["blocked_by"])
        self.assertEqual(result["gates"]["PRE"]["violation"], "precondition_not_met")
        self.assertIn("freq_locked", result["gates"]["PRE"]["detail"]["required_state"])

    def test_pre_blocks_unknown_action(self):
        result = evaluate_gates(
            self._record(state="unknown"),
            {"fn": "sq.not_a_real_function"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["gates"]["PRE"]["violation"], "unknown_action")

    def test_pre_allows_legal_action(self):
        result = evaluate_gates(
            self._record(state="unknown"),
            {"fn": "sq.spectroscopy"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["gates"]["PRE"]["ok"])
        self.assertEqual(result["allowed_edges"], ["unknown->freq_locked"])
        self.assertEqual(result["blocked_by"], [])

    def test_pre_reports_missing_evidence(self):
        result = evaluate_gates(
            self._record(state="unknown", evidence={}),
            {"fn": "sq.piamp"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        detail = result["gates"]["PRE"]["detail"]
        self.assertIn("freq_locked", detail["missing_evidence"])

    def test_pre_lists_pending_evidence_for_legal_edge(self):
        result = evaluate_gates(
            self._record(state="unknown"),
            {"fn": "sq.spectroscopy"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        pending = result["gates"]["PRE"]["detail"]["pending_evidence"]
        self.assertIn("freq_locked", pending)

    # ---- GATE-VER ----------------------------------------------------
    def test_ver_blocks_criteria_version_mismatch(self):
        result = evaluate_gates(
            self._record(state="unknown", criteria_version="0.9.0"),
            {"fn": "sq.spectroscopy"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertFalse(result["ok"])
        self.assertIn("VER", result["blocked_by"])
        self.assertEqual(
            result["gates"]["VER"]["violation"], "record_criteria_version_mismatch"
        )

    def test_ver_rerun_policy_does_not_block(self):
        machine = copy.deepcopy(load_state_machine())
        machine["gates"]["VER"]["on_version_mismatch"] = "rerun"
        result = evaluate_gates(
            self._record(state="unknown", criteria_version="0.9.0"),
            {"fn": "sq.spectroscopy"},
            criteria_version="1.0.0",
            now=self.NOW,
            machine=machine,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["needs_rerun"])
        self.assertEqual(result["blocked_by"], [])

    def test_ver_skipped_when_no_version_available(self):
        result = evaluate_gates(
            self._record(state="unknown", criteria_version=None),
            {"fn": "sq.spectroscopy"},
            criteria_version=None,
            now=self.NOW,
        )
        self.assertTrue(result["gates"]["VER"]["ok"])

    def test_ver_blocks_action_pinned_version(self):
        result = evaluate_gates(
            self._record(state="unknown"),
            {"fn": "sq.spectroscopy", "criteria_version": "0.9.0"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["gates"]["VER"]["violation"], "action_criteria_version_mismatch"
        )

    # ---- GATE-TTL ----------------------------------------------------
    def test_ttl_blocks_expired_state(self):
        result = evaluate_gates(
            self._record(state="freq_locked", expires_in=-1.0),
            {"fn": "sq.spectroscopy"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertFalse(result["ok"])
        self.assertIn("TTL", result["blocked_by"])
        self.assertEqual(result["gates"]["TTL"]["violation"], "state_expired")
        self.assertTrue(result["gates"]["TTL"]["detail"]["recovery"])

    def test_ttl_allows_state_without_expiry(self):
        result = evaluate_gates(
            self._record(state="unknown", expires_in=None),
            {"fn": "sq.spectroscopy"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertTrue(result["gates"]["TTL"]["ok"])
        self.assertTrue(result["ok"])

    # ---- 纯函数性 ----------------------------------------------------
    def test_gates_do_not_mutate_record(self):
        record = self._record(state="unknown")
        snapshot = copy.deepcopy(record)
        evaluate_gates(
            record,
            {"fn": "sq.piamp"},
            criteria_version="1.0.0",
            now=self.NOW,
        )
        self.assertEqual(record, snapshot)

    def test_resolve_edges_separates_legal_and_foreign(self):
        resolution = resolve_edges({"fn": "sq.spectroscopy"}, "freq_locked")
        self.assertIn("freq_locked--self--rescan", resolution["legal_ids"])
        self.assertIn("unknown->freq_locked", [e["id"] for e in resolution["foreign"]])

    def test_action_matches_alternative_function(self):
        edge = edge_by_id("unknown->freq_locked")
        self.assertTrue(action_matches_edge({"fn": "sq.pidf"}, edge))
        self.assertFalse(action_matches_edge({"fn": "sq.iqraw"}, edge))

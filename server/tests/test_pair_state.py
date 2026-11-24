"""pair（双比特 CZ）scope 的单测：结构 / 判据 / 状态推导 / 结论记录 / 执行层 HOLD。

分工（与 test_state_machine.py、test_router.py 一致）：
* 这里只测 pair scope 自己的东西（六态链、前置封顶、pair 证据翻译、DSL 缺口）；
* 闸门与 G0–G4 的通用口径在 test_router.py 里测，pair 只验证"接进 Router 后能 hold"。
"""
from __future__ import annotations

import copy
import time
import unittest
from unittest import mock

from services.common import metrics
from services.common.metrics import load_criteria
from services.common.pair_state import (
    PAIR_STATES,
    PREREQUISITE_STATE,
    build_pair_record,
    derive_pair_state,
    evidence_from_pair_records,
    execution_capability,
    normalize_pair,
    pair_key,
    pair_name,
    pair_prerequisites,
    state_table_pair,
)
from services.common.router import route
from services.common.state_machine import (
    SCOPE_PAIR,
    edges_for,
    machine_for_scope,
    state_table_extended,
)


def _prereq(*states: str):
    return pair_prerequisites(
        [{"qubit": "q%d" % i, "state": s} for i, s in enumerate(states)]
    )


def _ready():
    return _prereq("amp_calibrated", "state_discriminated")


class TestPairCriteria(unittest.TestCase):
    """三条 pair 判据（lab_criteria.json → metrics）。"""

    def test_cphase_locked_and_off_target(self):
        criteria = load_criteria()
        locked = metrics.evaluate("cphase", 3.14, criteria)
        self.assertEqual(locked["verdict"], "locked")
        self.assertTrue(locked["ok"])
        self.assertAlmostEqual(locked["target"], 3.14159, places=5)
        self.assertLess(locked["residual_rad"], locked["tolerance_rad"])

        off = metrics.evaluate("cphase", 2.0, criteria)
        self.assertEqual(off["verdict"], "off_target")
        self.assertFalse(off["ok"])

    def test_cz_leakage_bounded(self):
        criteria = load_criteria()
        self.assertEqual(metrics.evaluate("cz_leakage", 0.003, criteria)["verdict"], "bounded")
        self.assertEqual(metrics.evaluate("cz_leakage", 0.05, criteria)["verdict"], "too_leaky")

    def test_cz_gate_fidelity(self):
        criteria = load_criteria()
        self.assertEqual(
            metrics.evaluate("cz_gate_fidelity", 0.995, criteria)["verdict"], "verified"
        )
        self.assertEqual(
            metrics.evaluate("cz_gate_fidelity", 0.97, criteria)["verdict"], "degraded"
        )

    def test_pair_metrics_are_listed(self):
        levels = metrics.available_levels("cphase")
        self.assertTrue(levels)
        # 单比特 xeb 的 gate_fidelity 与 pair 的 cz_gate_fidelity 是两个口径
        self.assertNotEqual(
            metrics.evaluate("cz_gate_fidelity", 0.5, load_criteria())["verdict"],
            metrics.evaluate("xeb", {"p": 0.99, "gate_fidelity": 0.5}, load_criteria())["verdict"],
        )


class TestNormalizePair(unittest.TestCase):
    def test_accepted_shapes(self):
        self.assertEqual(normalize_pair({"qubits": ["q0", "q1"]})["pair"], "q0+q1")
        self.assertEqual(normalize_pair({"pair": "q0+q1"})["qubits"], ["q0", "q1"])
        self.assertEqual(normalize_pair({"name": "q0-q1"})["qubits"], ["q0", "q1"])
        self.assertEqual(
            normalize_pair({"control": "q0", "target": "q1"})["qubits"], ["q0", "q1"]
        )

    def test_order_is_preserved_for_display(self):
        forward = normalize_pair({"qubits": ["q0", "q1"]})
        backward = normalize_pair({"qubits": ["q1", "q0"]})
        self.assertEqual(forward["pair"], "q0+q1")
        self.assertEqual(backward["pair"], "q1+q0")
        # 身份键与顺序无关，用于查表/去重
        self.assertEqual(pair_key(forward["qubits"]), pair_key(backward["qubits"]))

    def test_coupler_and_errors(self):
        self.assertEqual(normalize_pair({"qubits": ["q0", "q1"], "coupler": "2"})["coupler"], 2)
        self.assertIsNone(normalize_pair({"qubits": ["q0", "q1"]})["coupler"])
        with self.assertRaises(ValueError):
            normalize_pair({"qubits": ["q0"]})
        with self.assertRaises(ValueError):
            normalize_pair({"qubits": ["q0", "q1", "q2"]})
        with self.assertRaises(ValueError):
            normalize_pair({"qubits": ["q0", "q1"], "coupler": "left"})
        with self.assertRaises(ValueError):
            normalize_pair({})

    def test_pair_name_skips_blank(self):
        self.assertEqual(pair_name(["q0", "", "q1"]), "q0+q1")


class TestPrerequisites(unittest.TestCase):
    def test_satisfied(self):
        result = _ready()
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["shortfall"], [])
        self.assertEqual(result["required_state"], PREREQUISITE_STATE)

    def test_shortfall_names_the_qubit(self):
        result = _prereq("amp_calibrated", "freq_locked")
        self.assertFalse(result["satisfied"])
        self.assertEqual(len(result["shortfall"]), 1)
        self.assertEqual(result["shortfall"][0]["qubit"], "q1")
        self.assertIn("freq_locked", result["reason"])

    def test_no_rows_is_not_satisfied(self):
        result = pair_prerequisites([])
        self.assertFalse(result["satisfied"])
        self.assertIn("未知", result["reason"])

    def test_state_discriminated_counts_as_satisfied(self):
        # "≥ amp_calibrated" 是索引比较，更高阶的状态也算齐备
        self.assertTrue(_prereq("characterized", "coherence_measured")["satisfied"])


class TestDerivePairState(unittest.TestCase):
    def test_six_state_chain(self):
        ready = _ready()
        self.assertEqual(derive_pair_state({}, prerequisites=ready)["state"], "pair_ready")
        self.assertEqual(
            derive_pair_state({"zpa_point": True}, prerequisites=ready)["state"], "cz_point_found"
        )
        self.assertEqual(
            derive_pair_state({"zpa_point": True, "cphase": 3.14}, prerequisites=ready)["state"],
            "phase_locked",
        )
        self.assertEqual(
            derive_pair_state(
                {"zpa_point": True, "cphase": 3.14, "frame_residual_rad": 0.01},
                prerequisites=ready,
            )["state"],
            "frame_corrected",
        )
        self.assertEqual(
            derive_pair_state(
                {
                    "zpa_point": True,
                    "cphase": 3.14,
                    "frame_residual_rad": 0.01,
                    "cz_leakage": 0.003,
                },
                prerequisites=ready,
            )["state"],
            "leakage_bounded",
        )
        self.assertEqual(
            derive_pair_state(
                {
                    "zpa_point": True,
                    "cphase": 3.14,
                    "frame_residual_rad": 0.01,
                    "cz_leakage": 0.003,
                    "cz_gate_fidelity": 0.995,
                },
                prerequisites=ready,
            )["state"],
            "gate_verified",
        )

    def test_state_index_matches_sequence(self):
        result = derive_pair_state({"zpa_point": True}, prerequisites=_ready())
        self.assertEqual(result["state_index"], PAIR_STATES.index(result["state"]))

    def test_bad_metric_does_not_advance(self):
        # cphase 偏到 2.0 rad：不算锁定，停在 cz_point_found
        result = derive_pair_state({"zpa_point": True, "cphase": 2.0}, prerequisites=_ready())
        self.assertEqual(result["state"], "cz_point_found")
        self.assertIn("off_target", result["reasons"][-1])

    def test_leakage_alone_is_not_enough(self):
        # 跳过 cphase 直接给泄漏：链路不跳级（frame_corrected 未满足 → 停在 pair_ready）
        result = derive_pair_state(
            {"zpa_point": True, "cz_leakage": 0.003}, prerequisites=_ready()
        )
        self.assertEqual(result["state"], "cz_point_found")

    def test_prerequisite_failure_caps_at_pair_ready(self):
        not_ready = _prereq("amp_calibrated", "freq_locked")
        result = derive_pair_state(
            {
                "zpa_point": True,
                "cphase": 3.14,
                "frame_residual_rad": 0.01,
                "cz_leakage": 0.003,
                "cz_gate_fidelity": 0.995,
            },
            prerequisites=not_ready,
        )
        self.assertEqual(result["state"], "pair_ready")
        self.assertIn("prerequisites", result["missing"])
        self.assertIn("封顶", "".join(result["reasons"]))

    def test_missing_and_next_action(self):
        result = derive_pair_state({"zpa_point": True}, prerequisites=_ready())
        self.assertEqual(result["missing"], ["cphase"])
        self.assertTrue(result["next_action"])
        self.assertEqual(result["missing_detail"][0]["key"], "cphase")

    def test_criteria_version_present(self):
        result = derive_pair_state({}, prerequisites=_ready())
        self.assertEqual(result["criteria_version"], load_criteria().get("version"))


class TestPairRecord(unittest.TestCase):
    def test_record_shape_and_scope(self):
        state = derive_pair_state({"zpa_point": True}, prerequisites=_ready())
        record = build_pair_record(
            qubits=["q0", "q1"],
            state_result=state,
            evidence={"zpa_point": True},
            sources={"zpa_point": {"run_id": "r1"}},
            coupler=2,
            now=1000.0,
        )
        self.assertEqual(record["scope"]["type"], SCOPE_PAIR)
        self.assertEqual(record["scope"]["qubits"], ["q0", "q1"])
        self.assertEqual(record["scope"]["coupler"], 2)
        self.assertEqual(record["pair"], "q0+q1")
        # qubit 字段保留成 pair 名，Router 的 $qubit 替换口径才能复用
        self.assertEqual(record["qubit"], "q0+q1")
        self.assertEqual(record["state"], "cz_point_found")
        self.assertEqual(record["decided_at"], 1000.0)
        self.assertIn("zpa_point", record["evidence"])
        self.assertIn("run_id", record["evidence"]["zpa_point"])

    def test_ttl_from_pair_table(self):
        state = derive_pair_state({"zpa_point": True}, prerequisites=_ready())
        record = build_pair_record(["q0", "q1"], state, now=1000.0)
        # cz_point_found 的 ttl_s = 86400
        self.assertEqual(record["expires_at"], 1000.0 + 86400)

    def test_pair_ready_never_expires(self):
        state = derive_pair_state({}, prerequisites=_ready())
        record = build_pair_record(["q0", "q1"], state, now=1000.0)
        self.assertIsNone(record["expires_at"])


class TestPairStructure(unittest.TestCase):
    def test_pair_view_has_own_edges(self):
        view = machine_for_scope(SCOPE_PAIR)
        self.assertEqual(len(edges_for("pair_ready", view)), 1)
        ids = {e["id"] for e in view["edges"]}
        self.assertIn("phase_locked--self--alt", ids)
        self.assertIn("leakage_bounded->cz_point_found", ids)
        self.assertIn("gate_verified->phase_locked", ids)
        self.assertEqual(len(view["edges"]), 13)
        self.assertEqual(len(view["states"]), 6)

    def test_qubit_and_pair_scopes_coexist(self):
        qubit = machine_for_scope("qubit")
        self.assertEqual(len(qubit["states"]), 6)
        self.assertIn("unknown", qubit["states"])
        self.assertNotIn("unknown", machine_for_scope(SCOPE_PAIR)["states"])

    def test_pair_edges_are_not_dsl_executable(self):
        view = machine_for_scope(SCOPE_PAIR)
        for edge in view["edges"]:
            self.assertFalse(edge["action"].get("dsl_support"), edge["id"])
            self.assertTrue(edge["action"].get("vendor_method"), edge["id"])

    def test_state_table_pair(self):
        table = state_table_pair()
        self.assertEqual(table["scope"], SCOPE_PAIR)
        self.assertEqual(table["states"], PAIR_STATES)
        self.assertEqual(table["prerequisite_state"], PREREQUISITE_STATE)
        self.assertFalse(table["execution"]["dsl_support"])

    def test_state_table_extended_pair(self):
        table = state_table_extended(scope=SCOPE_PAIR)
        self.assertEqual(table["scope"], SCOPE_PAIR)
        self.assertEqual(len(table["states"]), 6)
        self.assertEqual(len(table["edges"]), 13)
        self.assertIn("gates", table)


class TestExecutionCapability(unittest.TestCase):
    def test_hold_semantics(self):
        cap = execution_capability()
        self.assertFalse(cap["dsl_support"])
        self.assertFalse(cap["executable"])
        self.assertTrue(cap["requires_dsl_extension"])
        self.assertEqual(cap["blocked_by"], ["dsl:coupler_topology"])
        self.assertIn("耦合器", cap["dsl_gap"])
        self.assertEqual(cap["vendor"]["class"], "DCZTuner")

    def test_becomes_executable_when_dsl_supports(self):
        criteria = copy.deepcopy(load_criteria())
        criteria.setdefault("capabilities", {}).setdefault("two_qubit_vendor", {})[
            "dsl_support"
        ] = True
        cap = execution_capability(criteria)
        self.assertTrue(cap["executable"])
        self.assertEqual(cap["blocked_by"], [])


class TestEvidenceFromPairRecords(unittest.TestCase):
    def _record(self, **overrides):
        record = {
            "run_id": "r1",
            "qubits": ["q0", "q1"],
            "status": "qiskit_completed",
            "program": "",
            "manifest": {"functions": ["cz.sweep_sq_phase"]},
            "metrics": {"cphase": 3.14},
            "dataset_ids": ["d1"],
            "finished_at": 100.0,
        }
        record.update(overrides)
        return record

    def test_maps_pair_metrics(self):
        result = evidence_from_pair_records([self._record()], ["q0", "q1"])
        self.assertEqual(result["evidence"]["cphase"], 3.14)
        self.assertEqual(result["considered"], 1)
        self.assertEqual(result["sources"]["cphase"]["run_id"], "r1")

    def test_ignores_records_missing_one_qubit(self):
        result = evidence_from_pair_records(
            [self._record(qubits=["q0"])], ["q0", "q1"]
        )
        self.assertEqual(result["evidence"], {})
        self.assertEqual(result["considered"], 0)

    def test_ignores_unsuccessful_or_datasetless(self):
        result = evidence_from_pair_records(
            [
                self._record(run_id="r1", status="failed"),
                self._record(run_id="r2", dataset_ids=[]),
            ],
            ["q0", "q1"],
        )
        self.assertEqual(result["evidence"], {})
        self.assertEqual(result["ignored"], 2)
        self.assertIn("r1", result["ignored_runs"])

    def test_zpa_functions_set_point(self):
        result = evidence_from_pair_records(
            [
                self._record(
                    manifest={"functions": ["cz.initialize_gate"]},
                    metrics={},
                )
            ],
            ["q0", "q1"],
        )
        self.assertTrue(result["evidence"]["zpa_point"])

    def test_later_record_wins(self):
        older = self._record(run_id="r1", metrics={"cphase": 2.0}, finished_at=10.0)
        newer = self._record(run_id="r2", metrics={"cphase": 3.14}, finished_at=20.0)
        result = evidence_from_pair_records([newer, older], ["q0", "q1"])
        self.assertEqual(result["evidence"]["cphase"], 3.14)
        self.assertEqual(result["sources"]["cphase"]["run_id"], "r2")

    def test_single_bit_metrics_are_not_pair_evidence(self):
        result = evidence_from_pair_records(
            [self._record(metrics={"T1_us": 30.0, "contrast": 0.9})], ["q0", "q1"]
        )
        self.assertEqual(result["evidence"], {})


class TestRouterIntegration(unittest.TestCase):
    """pair 接进 Router：结构判定照常，执行层一律 HOLD（设计稿 §8.5）。"""

    def _decision(self, action, prerequisites=None, actuation=None):
        machine = machine_for_scope(SCOPE_PAIR)
        state = derive_pair_state({"zpa_point": True}, prerequisites=prerequisites or _ready())
        record = build_pair_record(["q0", "q1"], state, {"zpa_point": True}, {}, [], None, 2)
        return route(
            record,
            [action],
            record["criteria_version"],
            {},
            {"actuation": actuation or {"allow_execute": True, "allow_set_params": True},
             "machine": machine},
            machine,
        )

    def test_legal_cz_action_is_routed(self):
        decision = self._decision("cz.sweep_sq_phase")
        # 模板库为空 → G3；关键是从 pair 状态出得来，且边在 A(s) 内
        self.assertEqual(decision["current_state"], "cz_point_found")
        self.assertIn("cz_point_found->phase_locked", decision["allowed_edges"])

    def test_illegal_cz_action_rejected(self):
        decision = self._decision("cz.run_xeb")
        self.assertEqual(decision["decision"], "reject")
        self.assertEqual(decision["branch"], "G0")

    def test_expired_pair_record_is_held(self):
        machine = machine_for_scope(SCOPE_PAIR)
        state = derive_pair_state({"zpa_point": True}, prerequisites=_ready())
        record = build_pair_record(
            ["q0", "q1"], state, {"zpa_point": True}, {}, [], None, 2, now=time.time() - 90000
        )
        decision = route(
            record,
            ["cz.sweep_sq_phase"],
            record["criteria_version"],
            {},
            {"actuation": {"allow_execute": True, "allow_set_params": True}, "machine": machine},
            machine,
        )
        self.assertEqual(decision["decision"], "hold")
        self.assertIn("GATE-TTL", decision.get("blocked_by") or [])


class TestPairServerHandlers(unittest.TestCase):
    """HTTP 处理器层：返回结构 + 执行层强制 HOLD。"""

    def _service(self):
        from services.quantum_service.server import QuantumService

        # 只测纯计算分支，不启动服务、不连硬件
        return object.__new__(QuantumService)

    def _call(self, handler, payload, records=None):
        service = self._service()
        with mock.patch(
            "services.common.runrecord.RunRecordWriter.list_records",
            return_value=list(records or []),
        ):
            return handler(payload)

    def test_state_table_handler(self):
        result = self._call(self._service()._handle_qubit_pair_state_table, {})
        self.assertTrue(result["success"])
        self.assertEqual(result["scope"], "pair")
        self.assertEqual(len(result["edges"]), 13)

    def test_state_handler(self):
        result = self._call(
            self._service()._handle_qubit_pair_state,
            {"qubits": ["q0", "q1"], "coupler": 2},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["pair"], "q0+q1")
        self.assertEqual(result["state"]["state"], "pair_ready")
        self.assertFalse(result["prerequisites"]["satisfied"])
        self.assertIn("execution", result)
        self.assertFalse(result["execution"]["dsl_support"])

    def test_state_handler_rejects_wrong_arity(self):
        result = self._call(self._service()._handle_qubit_pair_state, {"qubits": ["q0"]})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "validation_error")

    def test_route_handler_forces_hold(self):
        result = self._call(
            self._service()._handle_qubit_pair_route,
            {"qubits": ["q0", "q1"], "coupler": 2, "actions": ["cz.initialize_gate"]},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertTrue(result["needs_human"])
        self.assertFalse(result["executable"])
        self.assertTrue(result["requires_dsl_extension"])
        self.assertEqual(result["blocked_by"], ["dsl:coupler_topology"])
        # 原判定保留下来，供上游补完 DSL 后对照
        self.assertIn("router_decision", result)
        self.assertIn("dsl_gap", result)

    def test_route_handler_keeps_reject(self):
        result = self._call(
            self._service()._handle_qubit_pair_route,
            {"qubits": ["q0", "q1"], "actions": ["cz.run_xeb"]},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["branch"], "G0")

    def test_route_handler_substitutes_params(self):
        # pair 边的 params_hint 用 $qubits / $coupler，选中的动作要落成真值
        machine = machine_for_scope(SCOPE_PAIR)
        state = derive_pair_state({"zpa_point": True}, prerequisites=_ready())
        record = build_pair_record(["q0", "q1"], state, {"zpa_point": True}, {}, [], None, 2)
        with mock.patch(
            "services.common.runrecord.RunRecordWriter.list_records", return_value=[]
        ), mock.patch.object(
            type(self._service()), "_pair_context",
            lambda svc, data: (None, {
                "pair": {"pair": "q0+q1", "qubits": ["q0", "q1"], "coupler": 2},
                "limit": 200, "records": [], "rows": [], "prerequisites": _ready(),
                "derived": {}, "evidence": {"zpa_point": True}, "sources": {},
                "state": state, "record": record,
            }),
        ), mock.patch(
            "services.common.router.route",
            return_value={
                "decision": "accept",
                "branch": "G1",
                "current_state": record["state"],
                "allowed_edges": ["cz_point_found->phase_locked"],
                "chosen_action": {
                    "fn": "cz.sweep_sq_phase",
                    "edge_id": "cz_point_found->phase_locked",
                    "params": {"qubits": "$qubits", "coupler": "$coupler"},
                },
                "reason": "模板命中",
                "needs_human": False,
            },
        ):
            result = self._service()._handle_qubit_pair_route({"qubits": ["q0", "q1"]})
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["chosen_action"]["params"]["qubits"], ["q0", "q1"])
        self.assertEqual(result["chosen_action"]["params"]["coupler"], 2)

    def test_handlers_survive_missing_ledger(self):
        # 台账缺失/为空不是错误：应退化成"前置未齐备 + 空证据"
        result = self._call(
            self._service()._handle_qubit_pair_state, {"pair": "q0+q1", "limit": "bad"}
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["records"]["limit"], 200)


if __name__ == "__main__":
    unittest.main()

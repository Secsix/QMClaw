"""measurement_loop 的单测：只测纯函数与「不碰网络」的分支。

网络调用一律不测（那是 demo_control_console 与各服务的集成测试），
这里守住的是「循环不会自己越过 allowed_edges」「退出码口径不漂」。
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import measurement_loop as ml  # noqa: E402


def _route_g3(**overrides):
    route = {
        "success": True,
        "decision": "ask_llm",
        "branch": "G3",
        "current_state": "unknown",
        "allowed_edges": ["unknown->freq_locked"],
        "llm_task": {
            "allowed_edges": ["unknown->freq_locked"],
            "allowed_actions": [
                {"fn": "sq.spectroscopy", "edge_id": "unknown->freq_locked", "intent": "四选一"},
                {"fn": "sq.spectroscopy_iq", "edge_id": "unknown->freq_locked", "intent": "四选一"},
            ],
            "instruction": "只在 allowed_edges 内选一个动作，产出必须落成 qwf",
        },
        "candidates": [{"fn": "sq.spectroscopy", "edge_id": "unknown->freq_locked"}],
    }
    route.update(overrides)
    return route


def _route_accept(**overrides):
    route = {
        "success": True,
        "decision": "accept",
        "branch": "G1",
        "current_state": "state_discriminated",
        "action_kind": "read",
        "chosen_action": {
            "fn": "sq.t1",
            "edge_id": "state_discriminated->coherence_measured",
            "params": {"qubit": "$qubit"},
        },
        "allowed_edges": ["state_discriminated->coherence_measured"],
    }
    route.update(overrides)
    return route


class TestFindAction(unittest.TestCase):
    def test_match_by_fn(self):
        action = ml.find_action(_route_g3(), fn="sq.spectroscopy")
        self.assertIsNotNone(action)
        self.assertEqual(action["edge_id"], "unknown->freq_locked")

    def test_match_by_edge(self):
        action = ml.find_action(_route_g3(), edge_id="unknown->freq_locked")
        self.assertEqual(action["fn"], "sq.spectroscopy")

    def test_fn_with_call_syntax_is_normalised(self):
        action = ml.find_action(_route_g3(), fn="sq.spectroscopy(freq=4.2)")
        self.assertIsNotNone(action)

    def test_outside_allowed_returns_none(self):
        self.assertIsNone(ml.find_action(_route_g3(), fn="sq.piamp"))

    def test_wrong_edge_returns_none(self):
        self.assertIsNone(ml.find_action(_route_g3(), fn="sq.spectroscopy", edge_id="freq_locked->amp_calibrated"))

    def test_empty_selector_returns_none(self):
        self.assertIsNone(ml.find_action(_route_g3()))


class TestAllowedActions(unittest.TestCase):
    def test_g3_uses_llm_task(self):
        fns = [a["fn"] for a in ml.allowed_actions(_route_g3())]
        self.assertEqual(fns, ["sq.spectroscopy", "sq.spectroscopy_iq"])

    def test_accept_falls_back_to_chosen(self):
        fns = [a["fn"] for a in ml.allowed_actions(_route_accept())]
        self.assertEqual(fns, ["sq.t1"])

    def test_accept_does_not_duplicate(self):
        route = _route_accept()
        route["llm_task"] = {"allowed_actions": [{"fn": "sq.t1", "edge_id": "e1"}]}
        self.assertEqual(len(ml.allowed_actions(route)), 1)

    def test_bad_input_is_empty(self):
        self.assertEqual(ml.allowed_actions(None), [])
        self.assertEqual(ml.allowed_actions({"success": True}), [])


class TestPlanRoundAccept(unittest.TestCase):
    def test_execute_with_resolved_qubit(self):
        plan = ml.plan_round(_route_accept(), qubit="q0")
        self.assertEqual(plan["kind"], ml.KIND_EXECUTE)
        self.assertEqual(plan["fn"], "sq.t1")
        self.assertEqual(plan["edge_id"], "state_discriminated->coherence_measured")
        self.assertEqual(plan["params"], {"qubit": "q0"})
        self.assertEqual(plan["action_kind"], "read")

    def test_accept_without_action_is_hold(self):
        route = _route_accept(chosen_action={})
        self.assertEqual(ml.plan_round(route, "state_discriminated")["kind"], ml.KIND_HOLD)


class TestPlanRoundG3(unittest.TestCase):
    def test_no_decision_stops(self):
        plan = ml.plan_round(_route_g3(), "unknown")
        self.assertEqual(plan["kind"], ml.KIND_NEEDS_DECISION)
        self.assertEqual([a["fn"] for a in plan["allowed"]], ["sq.spectroscopy", "sq.spectroscopy_iq"])
        self.assertIn("G3", plan["note"])

    def test_decide_fn_inside_allowed_executes(self):
        plan = ml.plan_round(
            _route_g3(),
            "unknown",
            qubit="q0",
            decide_fn="sq.spectroscopy",
            decide_params={"freq": [4.0, 3.0, -0.002]},
        )
        self.assertEqual(plan["kind"], ml.KIND_EXECUTE)
        self.assertEqual(plan["params"], {"freq": [4.0, 3.0, -0.002]})

    def test_decide_fn_outside_allowed_is_refused(self):
        plan = ml.plan_round(_route_g3(), "unknown", decide_fn="sq.piamp")
        self.assertEqual(plan["kind"], ml.KIND_NEEDS_DECISION)
        self.assertIn("不在 allowed_actions 内", plan["note"])
        self.assertIsNone(plan["fn"])

    def test_decide_edge_selects_that_action(self):
        plan = ml.plan_round(_route_g3(), "unknown", decide_edge="unknown->freq_locked")
        self.assertEqual(plan["fn"], "sq.spectroscopy")


class TestPlanRoundGates(unittest.TestCase):
    def test_reject(self):
        route = {
            "success": True,
            "decision": "reject",
            "branch": "G0",
            "current_state": "unknown",
            "reason": "动作不在合法边内",
            "missing": {"states": ["freq_locked"], "edges": ["unknown->freq_locked"]},
        }
        plan = ml.plan_round(route, "unknown")
        self.assertEqual(plan["kind"], ml.KIND_REJECTED)
        self.assertEqual(plan["missing"]["states"], ["freq_locked"])

    def test_hold_reports_gate_and_authorization(self):
        route = {
            "success": True,
            "decision": "hold",
            "branch": "G4",
            "current_state": "unknown",
            "reason": "闸门拦截",
            "blocked_by": ["GATE-VER"],
            "requires_authorization": True,
            "needs_human": True,
        }
        plan = ml.plan_round(route, "unknown")
        self.assertEqual(plan["kind"], ml.KIND_HOLD)
        self.assertIn("GATE-VER", plan["note"])
        self.assertIn("授权", plan["note"])
        self.assertIn("人工", plan["note"])

    def test_route_failure_is_hold(self):
        plan = ml.plan_round({"success": False, "error_code": "boom"}, "unknown")
        self.assertEqual(plan["kind"], ml.KIND_HOLD)
        self.assertIn("boom", plan["note"])

    def test_unknown_decision_needs_decision(self):
        plan = ml.plan_round({"success": True, "decision": "whatever"}, "unknown")
        self.assertEqual(plan["kind"], ml.KIND_NEEDS_DECISION)


class TestPlanRoundTerminal(unittest.TestCase):
    def test_characterized_is_done(self):
        plan = ml.plan_round(_route_accept(), "characterized")
        self.assertEqual(plan["kind"], ml.KIND_DONE)

    def test_custom_terminal(self):
        plan = ml.plan_round(_route_accept(), "coherence_measured", terminal="coherence_measured")
        self.assertEqual(plan["kind"], ml.KIND_DONE)


class TestStrictParams(unittest.TestCase):
    def test_missing_param_stops(self):
        plan = ml.plan_round(_route_g3(), "unknown", decide_fn="sq.spectroscopy", strict_params=True)
        self.assertEqual(plan["kind"], ml.KIND_NEEDS_DECISION)
        self.assertIn("freq", plan["note"])

    def test_param_given_passes(self):
        plan = ml.plan_round(
            _route_g3(),
            "unknown",
            decide_fn="sq.spectroscopy",
            decide_params={"freq": [4.0, 3.0, -0.002]},
            strict_params=True,
        )
        self.assertEqual(plan["kind"], ml.KIND_EXECUTE)


class TestExitCodes(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(ml.exit_code_for(ml.KIND_EXECUTE), 0)
        self.assertEqual(ml.exit_code_for(ml.KIND_DONE), 0)
        self.assertEqual(ml.exit_code_for(ml.KIND_REJECTED), 2)
        self.assertEqual(ml.exit_code_for(ml.KIND_NEEDS_DECISION), 3)
        self.assertEqual(ml.exit_code_for(ml.KIND_HOLD), 4)

    def test_unknown_kind_is_needs_decision(self):
        self.assertEqual(ml.exit_code_for("nonsense"), 3)


class TestBuildQwf(unittest.TestCase):
    def _validate(self, text):
        from services.workflow_service.dsl import parse_qwf

        try:
            from services.workflow_service.dsl import validate as maybe
        except Exception:  # pragma: no cover
            maybe = None
        validator = maybe if callable(maybe) else None
        if validator is None:  # 被子模块导入污染过，退回模块函数
            from services.workflow_service.dsl.validate import validate as validator
        ir = parse_qwf(text)
        diags = validator(ir, require_writes=True)
        errors = []
        for diag in diags:
            level = diag.get("level") if isinstance(diag, dict) else getattr(diag, "level", "")
            if str(level).lower() in ("error", "e"):
                errors.append(diag)
        return ir, errors

    def test_generates_valid_qwf_with_args(self):
        text = ml.build_qwf("sq.t1", "q0", {"zpa": 0.0})
        self.assertIn("workflow loop_sq_t1_", text)
        self.assertIn("args { zpa 0.0 }", text)
        self.assertIn("timeout 4200", text)
        ir, errors = self._validate(text)
        self.assertEqual(errors, [])
        self.assertIn("loop_sq_t1_", ir.get("id", ""))

    def test_range_param_renders(self):
        text = ml.build_qwf("sq.spectroscopy", "q0", {"freq": [4.0, 3.0, -0.002]})
        self.assertIn("freq range 4.0 3.0 -0.002", text)
        _ir, errors = self._validate(text)
        self.assertEqual(errors, [])

    def test_no_params_has_no_args_block(self):
        text = ml.build_qwf("sq.iqraw", "q0")
        self.assertNotIn("args {", text)
        _ir, errors = self._validate(text)
        self.assertEqual(errors, [])

    def test_bool_param_lowercase(self):
        text = ml.build_qwf("sq.s21", "q0", {"update": False})
        self.assertIn("update false", text)

    def test_slug_is_safe(self):
        # 调用语法被剥掉（qf 的 fn 只认函数名，参数走 args）
        self.assertEqual(ml.qwf_slug("sq.t1(delay=1)"), "sq_t1")
        self.assertEqual(ml.qwf_slug("cz.initialize_gate"), "cz_initialize_gate")
        self.assertEqual(ml.qwf_slug(""), "measure")


class TestPurity(unittest.TestCase):
    def test_plan_round_does_not_mutate_route(self):
        route = _route_g3()
        snapshot = copy.deepcopy(route)
        ml.plan_round(route, "unknown", decide_fn="sq.spectroscopy", decide_params={"freq": 4.2})
        self.assertEqual(route, snapshot)

    def test_params_are_copied_not_shared(self):
        route = _route_accept()
        plan = ml.plan_round(route, qubit="q0")
        plan["params"]["qubit"] = "q9"
        self.assertEqual(route["chosen_action"]["params"]["qubit"], "$qubit")


if __name__ == "__main__":
    unittest.main()

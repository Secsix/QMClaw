"""Router（`services/common/router.py`，BACKLOG O3 · P2）单测 —— 2026-11-24。

覆盖设计稿 §13 的 P2 验收项：

1. **非法边必被 G0 拒** —— 候选动作不在 A(s) 内时返回 ``reject``，并给出 ``missing``；
2. **G1/G2/G3/G4 各有分支用例** —— 模板命中且稳定 / 模板命中但参数漂移 /
   无适用模板交 LLM / 越权或连续失败转人工；
3. **不变式③** —— ``chosen_action`` 的边 ∈ ``allowed_edges``；
4. **确定性** —— 相同输入永远得到相同输出（不变式④）；
5. **写授权口径（2026-11-24 拍板「a」）** —— 只读动作的 accept 免授权，
   写参数 / 未登记动作仍要闸门，判定依据是 ``action_semantics``（配置优先）。

跑法：
    python -m unittest tests.test_router -v
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.common.router import (  # noqa: E402
    _action_kind,
    _action_semantics,
    _authorization_for,
    route,
)
from services.common.state_machine import load_state_machine  # noqa: E402

NOW = 1_000_000.0


def _machine(templates=None):
    """真实结构表（边表 / 闸门）+ 可选模板库。"""
    machine = copy.deepcopy(load_state_machine())
    if templates is not None:
        machine["templates"] = copy.deepcopy(templates)
    return machine


def _record(state, qubit="q0", evidence=None, criteria_version="1.0.0", expires_at=None):
    return {
        "qubit": qubit,
        "scope": {"type": "qubit", "qubits": [qubit], "coupler": None},
        "state": state,
        "state_index": 0,
        "state_version": "1.0.0",
        "criteria_version": criteria_version,
        "evidence": copy.deepcopy(evidence or {}),
        "conflicts": [],
        "reasons": [],
        "missing": [],
        "decided_at": NOW - 10.0,
        "expires_at": expires_at,
    }


def _env(allow_execute=True, now=NOW, **extra):
    env = {
        "actuation": {"allow_execute": allow_execute, "allow_set_params": allow_execute},
        "now": now,
    }
    env.update(extra)
    return env


def _template(edge, fn, **kwargs):
    tpl = {
        "id": "T-" + edge,
        "edge": edge,
        "action": {"fn": fn, "params": {}},
        "scope": "qubit",
        "prior_success_rate": 0.8,
        "observed": {"success": 5, "failure": 0},
        "success_rate": 0.8,
        "status": "normal",
        "promote_threshold": 0.8,
        "consecutive_success_for_promote": 2,
        "degrade_threshold": 0.3,
        "consecutive_failure_for_degrade": 2,
        "source": "manual",
    }
    tpl.update(kwargs)
    return tpl


class TestRouterGates(unittest.TestCase):
    """闸门先于一切：PRE 逐候选，VER / TTL 记录级。"""

    def test_ttl_expired_holds_g4(self):
        result = route(
            _record("unknown", expires_at=NOW - 1.0),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(),
        )
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertTrue(result["needs_human"])
        self.assertIn("GATE-TTL", result["blocked_by"])

    def test_ver_version_mismatch_holds_g4(self):
        result = route(
            _record("unknown", criteria_version="0.9.0"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(),
        )
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertIn("GATE-VER", result["blocked_by"])

    def test_gates_reported(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(),
        )
        gates = {g["gate"]: g["passed"] for g in result["gates"]}
        self.assertIn("GATE-PRE", gates)
        self.assertTrue(gates["GATE-PRE"])


class TestRouterG0(unittest.TestCase):
    """G0：非法边必拒，且要说明还缺什么。"""

    def test_illegal_edge_rejected(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.piamp"}],  # piamp 只在 freq_locked 起合法
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(),
        )
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["branch"], "G0")
        self.assertIn("freq_locked", result["missing"]["states"])

    def test_unknown_action_rejected(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.not_in_edge_table"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(),
        )
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["branch"], "G0")

    def test_empty_actions_rejected(self):
        result = route(
            _record("unknown"),
            [],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(),
        )
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["branch"], "G0")
        self.assertIn("没有候选动作", result["reason"])

    def test_probability_never_bypasses_g0(self):
        """模板再稳也不能救非法边（G0 不许被概率/LLM 绕过）。"""
        machine = _machine([_template("unknown->freq_locked", "sq.spectroscopy")])
        result = route(
            _record("unknown"),
            [{"fn": "sq.piamp"}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {"sq.piamp": 99}},
            environment=_env(),
            machine=machine,
        )
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["branch"], "G0")


class TestRouterG1(unittest.TestCase):
    """G1：模板命中且近期稳定 → accept。"""

    def _template(self, **kwargs):
        return _template("unknown->freq_locked", "sq.spectroscopy", **kwargs)

    def test_g1_accept(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {"sq.spectroscopy": 3}},
            environment=_env(),
            machine=_machine([self._template()]),
        )
        self.assertEqual(result["decision"], "accept")
        self.assertEqual(result["branch"], "G1")
        self.assertEqual(result["chosen_action"]["fn"], "sq.spectroscopy")
        self.assertFalse(result["needs_human"])

    def test_g1_chosen_edge_in_allowed_edges(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {"sq.spectroscopy": 3}},
            environment=_env(),
            machine=_machine([self._template()]),
        )
        self.assertIn(result["chosen_action"]["edge_id"], result["allowed_edges"])

    def test_g1_without_authorization_holds(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {"sq.spectroscopy": 3}},
            environment=_env(allow_execute=False),
            machine=_machine([self._template()]),
        )
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertTrue(result["needs_human"])
        self.assertTrue(result["requires_authorization"])

    def test_g1_needs_recent_streak(self):
        """成功率够但近期没连续成功 → 不算稳定 → 落 G3。"""
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {"sq.spectroscopy": 0}},
            environment=_env(),
            machine=_machine([self._template()]),
        )
        self.assertEqual(result["branch"], "G3")

    def test_retired_template_is_ignored(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {"sq.spectroscopy": 9}},
            environment=_env(),
            machine=_machine([self._template(status="retired")]),
        )
        self.assertEqual(result["branch"], "G3")


class TestRouterG2(unittest.TestCase):
    """G2：模板命中但参数漂移 → accept + requires_param_refresh。"""

    def test_g2_param_drift(self):
        machine = _machine([
            _template(
                "amp_calibrated->state_discriminated",
                "sq.iqraw",
                observed={"success": 0, "failure": 0},
                prior_success_rate=0.5,
                params_evidence={"contrast": 0.9},
            )
        ])
        record = _record(
            "amp_calibrated",
            evidence={"contrast": {"key": "contrast", "value": 0.5}},
        )
        result = route(
            record,
            [{"fn": "sq.iqraw"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=machine,
        )
        self.assertEqual(result["decision"], "accept")
        self.assertEqual(result["branch"], "G2")
        self.assertTrue(result["requires_param_refresh"])
        self.assertEqual(result["param_drift"]["key"], "contrast")


class TestRouterG3(unittest.TestCase):
    """G3：合法边存在但没有适用模板 → ask_llm。"""

    def test_g3_when_templates_empty(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(templates=[]),
        )
        self.assertEqual(result["decision"], "ask_llm")
        self.assertEqual(result["branch"], "G3")
        self.assertIn("unknown->freq_locked", result["allowed_edges"])

    def test_g3_llm_task_confined_to_allowed_edges(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(),
            machine=_machine(templates=[]),
        )
        task = result["llm_task"]
        self.assertEqual(task["allowed_edges"], result["allowed_edges"])
        for item in task["allowed_actions"]:
            self.assertIn(item["edge_id"], result["allowed_edges"])

    def test_g3_not_blocked_by_missing_authorization(self):
        """G3 只产出候选 qwf，不该被写闸门挡成 hold。"""
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={},
            environment=_env(allow_execute=False),
            machine=_machine(templates=[]),
        )
        self.assertEqual(result["decision"], "ask_llm")


class TestRouterG4(unittest.TestCase):
    """G4：越权 / 连续失败 / 自环次数用尽 → hold + needs_human。"""

    def test_self_loop_exhausted(self):
        result = route(
            _record("freq_locked"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"attempts": {"sq.spectroscopy": 3}},
            environment=_env(),
            machine=_machine(),
        )
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertTrue(result["needs_human"])
        self.assertTrue(result["exhausted"])

    def test_consecutive_failures_exhausted(self):
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"consecutive_failures": {"sq.spectroscopy": 3}},
            environment=_env(),
            machine=_machine(),
        )
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertTrue(result["needs_human"])


class TestRouterActionAuthorization(unittest.TestCase):
    """G4 写授权口径：按动作的写语义判定，只读动作的 accept 免授权。

    配置真源 = ``config/state_machine.json`` 的 ``action_semantics``；
    这里同时验证「配置改了判定就跟着变」，避免把它写成代码里的死规则。
    """

    # ---- 工具 ----

    def _semantics(self, machine=None, **overrides):
        machine = machine or _machine()
        if overrides:
            sem = copy.deepcopy(machine.get("action_semantics") or {})
            sem.update(overrides)
            machine = copy.deepcopy(machine)
            machine["action_semantics"] = sem
        return _action_semantics(machine)

    def _read_accept(self, actuation, machine=None):
        """state_discriminated --sq.t1--> coherence_measured：纯测量动作。"""
        return route(
            _record("state_discriminated"),
            [{"fn": "sq.t1"}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {"sq.t1": 3}},
            environment={"actuation": actuation, "now": NOW},
            machine=machine or _machine(
                [_template("state_discriminated->coherence_measured", "sq.t1")]
            ),
        )

    def _write_accept(self, actuation, fn="sq.piamp"):
        return route(
            _record("freq_locked"),
            [{"fn": fn}],
            criteria={"version": "1.0.0"},
            history={"success_streak": {fn: 3}},
            environment={"actuation": actuation, "now": NOW},
            machine=_machine([_template("freq_locked->amp_calibrated", fn)]),
        )

    # ---- 只读动作 ----

    def test_read_only_accept_needs_no_authorization(self):
        """两个闸门都关着，纯测量动作的 accept 照常成立。"""
        result = self._read_accept({"allow_execute": False, "allow_set_params": False})
        self.assertEqual(result["decision"], "accept")
        self.assertEqual(result["branch"], "G1")
        self.assertEqual(result["action_kind"], "read")
        self.assertFalse(result["requires_authorization"])
        self.assertFalse(result["needs_human"])
        self.assertTrue(result["authorization"]["satisfied"])
        self.assertEqual(result["authorization"]["needs_any"], [])

    def test_read_only_kind_survives_call_form(self):
        """``sq.t1(q0)`` 这种调用写法也要认出来，别因为带括号就落 unknown。"""
        sem = self._semantics()
        self.assertEqual(_action_kind(sem, {"fn": "sq.t1(q0)"}), "read")
        self.assertEqual(_action_kind(sem, {"fn": " sq.iqraw "}), "read")

    # ---- 写参数动作 ----

    def test_write_accept_still_holds_without_gate(self):
        result = self._write_accept({"allow_execute": False, "allow_set_params": False})
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertEqual(result["action_kind"], "write")
        self.assertTrue(result["requires_authorization"])
        self.assertTrue(result["needs_human"])
        self.assertEqual(result["authorization"]["missing"], ["allow_set_params"])

    def test_write_accept_passes_when_set_params_open(self):
        result = self._write_accept({"allow_execute": False, "allow_set_params": True})
        self.assertEqual(result["decision"], "accept")
        self.assertFalse(result["requires_authorization"])

    def test_write_kind_ignores_allow_execute(self):
        """写参数只认 allow_set_params：只开 allow_execute 不算授权。"""
        result = self._write_accept({"allow_execute": True, "allow_set_params": False})
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertEqual(result["authorization"]["missing"], ["allow_set_params"])

    # ---- 未登记动作：保守 ----

    def test_unregistered_fn_falls_back_to_unknown(self):
        sem = self._semantics()
        self.assertEqual(_action_kind(sem, {"fn": "sq.some_new_experiment"}), "unknown")
        auth = _authorization_for(
            sem,
            {"fn": "sq.some_new_experiment"},
            {"allow_execute": False, "allow_set_params": False},
        )
        self.assertEqual(auth["kind"], "unknown")
        self.assertFalse(auth["satisfied"])
        self.assertEqual(sorted(auth["needs_any"]), ["allow_execute", "allow_set_params"])

    def test_unknown_kind_accept_holds(self):
        """未登记动作落进 unknown → 闸门都关着时不放行。

        同一份 t1 输入，只把配置里的 kind 改成 unknown，判定就从 accept 变成 hold。
        """
        machine = copy.deepcopy(
            _machine([_template("state_discriminated->coherence_measured", "sq.t1")])
        )
        machine["action_semantics"]["fns"]["sq.t1"] = "unknown"
        result = self._read_accept(
            {"allow_execute": False, "allow_set_params": False}, machine=machine
        )
        self.assertEqual(result["action_kind"], "unknown")
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertTrue(result["requires_authorization"])
        self.assertEqual(
            sorted(result["authorization"]["needs_any"]),
            ["allow_execute", "allow_set_params"],
        )

    # ---- 配置是真源 ----

    def test_config_override_changes_verdict(self):
        """把 sq.t1 在配置里改成 write，同一份输入就该被 G4 拦下。"""
        machine = copy.deepcopy(_machine([_template("state_discriminated->coherence_measured", "sq.t1")]))
        machine["action_semantics"]["fns"]["sq.t1"] = "write"
        result = self._read_accept({"allow_execute": False, "allow_set_params": False}, machine=machine)
        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["branch"], "G4")
        self.assertEqual(result["action_kind"], "write")

    def test_default_kind_from_config(self):
        """配置把 default_kind 改成 read 时，未登记动作也按 read 处理。"""
        sem = self._semantics(default_kind="read")
        self.assertEqual(_action_kind(sem, {"fn": "sq.brand_new"}), "read")

    def test_kinds_needs_any_from_config(self):
        """kinds 的 needs_any 可被配置改掉（例如 write 要求两个闸门都开）。"""
        sem = self._semantics(kinds={"write": {"needs_any": ["allow_execute", "allow_set_params"]}})
        auth = _authorization_for(
            sem, {"fn": "sq.piamp"}, {"allow_execute": True, "allow_set_params": False}
        )
        self.assertEqual(auth["kind"], "write")
        self.assertFalse(auth["satisfied"])
        self.assertEqual(auth["missing"], ["allow_set_params"])

    # ---- 不误伤 G3 / G0 ----

    def test_g3_not_blocked_by_gate(self):
        """G3 只是产出候选 qwf，闸门关着也照样 ask_llm。"""
        result = route(
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={},
            environment={"actuation": {"allow_execute": False, "allow_set_params": False},
                         "now": NOW},
            machine=_machine(templates=[]),
        )
        self.assertEqual(result["decision"], "ask_llm")
        self.assertFalse(result["requires_authorization"])


class TestRouterInvariants(unittest.TestCase):
    """不变式与纯函数性质。"""

    def _args(self):
        return (
            _record("unknown"),
            [{"fn": "sq.spectroscopy"}],
            {"version": "1.0.0"},
            {"success_streak": {"sq.spectroscopy": 3}},
            _env(),
            _machine([_template("unknown->freq_locked", "sq.spectroscopy")]),
        )

    def test_same_input_same_output(self):
        args = self._args()
        first = route(*args)
        second = route(*args)
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False),
                         json.dumps(second, sort_keys=True, ensure_ascii=False))

    def test_inputs_not_mutated(self):
        args = self._args()
        snapshot = copy.deepcopy(args)
        route(*args)
        self.assertEqual(args[0], snapshot[0])
        self.assertEqual(args[1], snapshot[1])
        self.assertEqual(args[2], snapshot[2])
        self.assertEqual(args[3], snapshot[3])
        self.assertEqual(args[4], snapshot[4])
        self.assertEqual(args[5], snapshot[5])

    def test_evidence_ref_dedup(self):
        record = _record(
            "unknown",
            evidence={
                "freq_locked": {"key": "freq_locked", "value": 1, "run_id": "run-1"},
                "contrast": {"key": "contrast", "value": 0.9, "run_id": "run-2"},
            },
        )
        result = route(
            record,
            [{"fn": "sq.spectroscopy"}],
            criteria={"version": "1.0.0"},
            history={"evidence_ref": ["run-2", "run-3"]},
            environment=_env(),
            machine=_machine(),
        )
        refs = result["evidence_ref"]
        self.assertEqual(len(refs), len(set(refs)))
        self.assertIn("run-1", refs)
        self.assertIn("run-3", refs)


if __name__ == "__main__":
    unittest.main()

"""Router（`services/common/router.py`，BACKLOG O3 · P2）单测 —— 2026-11-24。

覆盖设计稿 §13 的 P2 验收项：

1. **非法边必被 G0 拒** —— 候选动作不在 A(s) 内时返回 ``reject``，并给出 ``missing``；
2. **G1/G2/G3/G4 各有分支用例** —— 模板命中且稳定 / 模板命中但参数漂移 /
   无适用模板交 LLM / 越权或连续失败转人工；
3. **不变式③** —— ``chosen_action`` 的边 ∈ ``allowed_edges``；
4. **确定性** —— 相同输入永远得到相同输出（不变式④）。

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

from services.common.router import route  # noqa: E402
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

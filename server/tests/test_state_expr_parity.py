"""声明式状态表达式（`state_expr.py`）的测试。

重点是 **parity**：`derive_state_declarative` 必须与 `qubit_state.derive_state`
逐条一致——这是把 scope 的 `engine` 从 `legacy` 切到 `config` 的前置闸门。
"""

from __future__ import annotations

import itertools
import unittest

from services.common import qubit_state, state_expr
from services.common.metrics import load_criteria
from services.common.machine_profile import capability_tags
from services.common.state_machine import load_state_machine


MACHINE = load_state_machine()
CRITERIA = load_criteria()
STATES = MACHINE["states"]

#: 覆盖"缺证据 / 字符串 / 布尔 / 边界数值"的取值集合。
FREQ_VALUES = [None, True, False, "yes", "no", 1, 0]
AMP_VALUES = [None, True, False, "ok", 0]
CONTRAST_VALUES = [None, 0.1, 0.3, 0.5, 0.85]
T1_VALUES = [None, 5.0, 19.9, 25.0, 60.0]
T2_VALUES = [None, 0.5, 1.0, 10.0]
XEB_P_VALUES = [None, 0.98, 0.995]
XEB_GF_VALUES = [None, 0.97, 0.995]


def _evidence(**kwargs):
    ev = {}
    for key, value in kwargs.items():
        if value is not None:
            ev[key] = value
    return ev


def _derive_declarative(evidence):
    return state_expr.derive_state_declarative(
        STATES, evidence, CRITERIA, scope="qubit", require_when=True
    )


class TestFlagParity(unittest.TestCase):
    """`state_expr.flag` 与 `qubit_state._flag` 必须完全一致。"""

    def test_matrix(self):
        values = [None, True, False, 0, 1, 2, -1, 0.0, 0.5, "", "1", "true", " yes ", "NO",
                  "ok", "locked", "done", "other", [], {}, [1]]
        for value in values:
            evidence = {} if value is None else {"k": value}
            self.assertEqual(
                state_expr.flag(evidence, "k"),
                qubit_state._flag(evidence, "k"),
                "flag(%r) 不一致" % (value,),
            )


class TestLeaves(unittest.TestCase):
    def test_empty_is_true(self):
        self.assertTrue(state_expr.eval_when(None, {}))
        self.assertTrue(state_expr.eval_when({"all_of": []}, {}))

    def test_flag_leaf(self):
        self.assertTrue(state_expr.eval_when({"flag": "freq_locked"}, {"freq_locked": True}))
        self.assertFalse(state_expr.eval_when({"flag": "freq_locked"}, {}))

    def test_is_leaf(self):
        self.assertTrue(state_expr.eval_when({"evidence": "amp_calibrated", "is": False}, {}))
        self.assertFalse(state_expr.eval_when({"evidence": "amp_calibrated", "is": True}, {}))

    def test_verdict_leaves(self):
        ev = {"contrast": 0.85}
        self.assertTrue(state_expr.eval_when({"evidence": "contrast", "verdict_in": ["good"]}, ev, CRITERIA))
        self.assertFalse(state_expr.eval_when({"evidence": "contrast", "verdict_in": ["unusable"]}, ev, CRITERIA))
        self.assertTrue(state_expr.eval_when({"evidence": "contrast", "verdict_not_in": ["unusable"]}, ev, CRITERIA))
        # 缺证据时 verdict_not_in 不成立（与 legacy 的 "contrast is not None" 一致）
        self.assertFalse(state_expr.eval_when({"evidence": "contrast", "verdict_not_in": ["unusable"]}, {}, CRITERIA))

    def test_numeric_leaves(self):
        ev = {"t1_us": 25.0}
        self.assertTrue(state_expr.eval_when({"evidence": "t1_us", "ge": 20}, ev))
        self.assertFalse(state_expr.eval_when({"evidence": "t1_us", "ge": 30}, ev))
        self.assertTrue(state_expr.eval_when({"evidence": "t1_us", "le": 30}, ev))
        self.assertTrue(state_expr.eval_when({"evidence": "t1_us", "gt": 24.9, "lt": 25.1}, ev))
        self.assertFalse(state_expr.eval_when({"evidence": "t1_us", "ge": 1}, {}))

    def test_ok_leaf(self):
        self.assertTrue(state_expr.eval_when({"evidence": "contrast", "ok": True}, {"contrast": 0.3}, CRITERIA))
        self.assertFalse(state_expr.eval_when({"evidence": "contrast", "ok": True}, {"contrast": 0.1}, CRITERIA))
        self.assertFalse(state_expr.eval_when({"evidence": "contrast", "ok": True}, {}, CRITERIA))

    def test_metric_leaf(self):
        ev = {"xeb_gate_fidelity": 0.995}
        self.assertTrue(state_expr.eval_when({"metric": "xeb", "ok": True}, ev, CRITERIA))
        self.assertFalse(state_expr.eval_when({"metric": "xeb", "ok": True}, {}, CRITERIA))

    def test_xeb_both_keys(self):
        # 只有 p、没有 gate_fidelity 时也应该能判定（legacy 的 evaluate_xeb 支持）
        ev = {"xeb_p": 0.995}
        self.assertTrue(state_expr.eval_when({"evidence": "xeb_gate_fidelity", "ok": True}, ev, CRITERIA))

    def test_groups(self):
        ev = {"contrast": 0.85, "t1_us": 25.0}
        self.assertTrue(state_expr.eval_when(
            {"all_of": [{"evidence": "contrast", "ok": True}, {"evidence": "t1_us", "ok": True}]}, ev, CRITERIA))
        self.assertFalse(state_expr.eval_when(
            {"all_of": [{"evidence": "contrast", "ok": True}, {"evidence": "t2_star_us", "ok": True}]}, ev, CRITERIA))
        self.assertTrue(state_expr.eval_when(
            {"any_of": [{"evidence": "t2_star_us", "ok": True}, {"evidence": "t1_us", "ok": True}]}, ev, CRITERIA))
        self.assertTrue(state_expr.eval_when(
            {"none_of": [{"evidence": "t2_star_us", "ok": True}]}, ev, CRITERIA))

    def test_prerequisite(self):
        when = {"prerequisite": "qubit>=state_discriminated"}
        # 没有前置信息时按满足处理（老行为不变）
        self.assertTrue(state_expr.eval_when(when, {}))
        self.assertTrue(state_expr.eval_when(when, {}, prerequisites={"qubit": "state_discriminated"}))
        self.assertTrue(state_expr.eval_when(when, {}, prerequisites={"qubit": "characterized"}))
        self.assertFalse(state_expr.eval_when(when, {}, prerequisites={"qubit": "amp_calibrated"}))
        self.assertFalse(state_expr.eval_when(when, {}, prerequisites={}))
        self.assertTrue(state_expr.eval_when(
            {"prerequisite": "qubit==freq_locked"}, {}, prerequisites={"qubit": "freq_locked"}))


class TestErrors(unittest.TestCase):
    def test_unknown_key(self):
        with self.assertRaises(ValueError):
            state_expr.eval_when({"bogus": 1}, {})

    def test_not_a_dict(self):
        with self.assertRaises(ValueError):
            state_expr.eval_when("freq_locked", {})

    def test_evidence_without_condition(self):
        with self.assertRaises(ValueError):
            state_expr.eval_when({"evidence": "contrast"}, {"contrast": 0.9})

    def test_bad_prerequisite(self):
        with self.assertRaises(ValueError):
            state_expr.eval_when({"prerequisite": "qubit-state"}, {}, prerequisites={"qubit": "unknown"})

    def test_unknown_metric_is_false_not_crash(self):
        # 判据表里没有这个指标时按"未通过"处理，但不炸
        self.assertFalse(state_expr.eval_when({"evidence": "no_such_metric", "ok": True}, {"no_such_metric": 0.02}))


class TestCompileStates(unittest.TestCase):
    def test_by_index(self):
        items = state_expr.compile_states({"b": {"index": 1}, "a": {"index": 0}})
        self.assertEqual([it["name"] for it in items], ["a", "b"])

    def test_list_form(self):
        items = state_expr.compile_states([{"name": "a"}, {"name": "b"}])
        self.assertEqual([it["name"] for it in items], ["a", "b"])

    def test_list_requires_name(self):
        with self.assertRaises(ValueError):
            state_expr.compile_states([{"when": None}])

    def test_order_fallback(self):
        items = state_expr.compile_states({"a": {}, "b": {}}, order=["b", "a"])
        self.assertEqual([it["name"] for it in items], ["b", "a"])


class TestConfigWhens(unittest.TestCase):
    def test_every_state_has_when(self):
        for name, body in STATES.items():
            self.assertIn("when", body, "%s 缺少声明式 when" % name)

    def test_config_engine_still_legacy(self):
        self.assertEqual(MACHINE["scopes"]["qubit"]["engine"], "legacy")

    def test_full_evidence_matches_state_table(self):
        ev = {
            "freq_locked": True,
            "amp_calibrated": True,
            "contrast": 0.85,
            "t1_us": 25.0,
            "t2_star_us": 10.0,
            "xeb_gate_fidelity": 0.995,
        }
        self.assertEqual(_derive_declarative(ev)["state"], "characterized")


class TestParityLegacy(unittest.TestCase):
    """枚举组合，声明式结果必须与 legacy 的 derive_state 一致。"""

    def test_grid(self):
        checked = 0
        mismatched = []
        for freq, amp, contrast, t1, t2, xeb_p, xeb_gf in itertools.product(
            FREQ_VALUES, AMP_VALUES, CONTRAST_VALUES, T1_VALUES, T2_VALUES, XEB_P_VALUES, XEB_GF_VALUES
        ):
            evidence = _evidence(
                freq_locked=freq,
                amp_calibrated=amp,
                contrast=contrast,
                t1_us=t1,
                t2_star_us=t2,
                xeb_p=xeb_p,
                xeb_gate_fidelity=xeb_gf,
            )
            legacy = qubit_state.derive_state(evidence, CRITERIA)
            declarative = _derive_declarative(evidence)
            checked += 1
            if legacy["state"] != declarative["state"]:
                mismatched.append((dict(evidence), legacy["state"], declarative["state"]))
                if len(mismatched) > 5:
                    break
        self.assertGreater(checked, 5000)
        self.assertEqual(mismatched, [], "声明式与 legacy 不一致：%r" % (mismatched[:3],))

    def test_each_state_reachable(self):
        seen = set()
        for freq, amp in itertools.product([None, True], [None, True]):
            for contrast in CONTRAST_VALUES:
                for t1 in T1_VALUES:
                    for t2 in T2_VALUES:
                        for gf in XEB_GF_VALUES:
                            evidence = _evidence(
                                freq_locked=freq,
                                amp_calibrated=amp,
                                contrast=contrast,
                                t1_us=t1,
                                t2_star_us=t2,
                                xeb_gate_fidelity=gf,
                            )
                            seen.add(_derive_declarative(evidence)["state"])
        self.assertEqual(seen, set(qubit_state.STATES))

    def test_amp_from_contrast_only(self):
        # 只有 contrast 也能过 amp_calibrated；weak_but_usable（ok=True）直接到态区分
        self.assertEqual(_derive_declarative({"contrast": 0.3})["state"], "state_discriminated")
        self.assertEqual(_derive_declarative({"contrast": 0.05})["state"], "unknown")
        # amp_calibrated 只能靠显式标记：contrast 只要 >= unusable，判据就 ok=True 直接到态区分
        self.assertEqual(_derive_declarative({"amp_calibrated": True})["state"], "amp_calibrated")
        self.assertEqual(_derive_declarative({"contrast": 0.25})["state"], "state_discriminated")


class TestRequireWhen(unittest.TestCase):
    def test_missing_when_is_reported(self):
        states = {
            "a": {"index": 0},
            "b": {"index": 1},
        }
        result = state_expr.derive_state_declarative(states, {"x": 1}, CRITERIA, require_when=True)
        self.assertEqual(result["state"], "a")
        self.assertEqual(result["missing_when"], ["b"])

    def test_trace_present(self):
        when = {"all_of": [{"flag": "freq_locked"}]}
        result = state_expr.explain_when(when, {"freq_locked": True})
        self.assertTrue(result["ok"])
        self.assertTrue(result["trace"])


class TestCapabilityTags(unittest.TestCase):
    def test_list_form(self):
        self.assertEqual(capability_tags({"capabilities": ["tunable", "coupler"]}),
                         {"tunable": True, "coupler": True})

    def test_dict_form(self):
        self.assertEqual(capability_tags({"capabilities": {"coupler": True, "tunable": False}}),
                         {"coupler": True, "tunable": False})

    def test_missing(self):
        self.assertEqual(capability_tags({"capabilities": None}), {})
        self.assertEqual(capability_tags({}), {})


if __name__ == "__main__":
    unittest.main()

"""`state_route` 节点单测 —— 2026-11-24（状态机 Router 接入运行时决策链）。

覆盖设计稿 §7.6 的落地口径：

1. **accept（G1/G2）不问 LLM** —— 直接用 Router 给的动作；
2. **ask_llm（G3）越界即拒绝** —— LLM 推荐不在 ``allowed_actions`` 内的 fn 一律丢弃，
   节点转 ``needs_human``，绝不放行越界动作；
3. **reject/`G0` 与 hold/`G4` 转人工** —— 带上 ``missing`` / ``requires_authorization``；
4. **路由服务不可用 = 失败**（不下发任何动作），不是"猜一个"；
5. **IR 落点** —— ``decide ... mode "state_route"`` 在 ``prepare()`` 里就建成
   ``state_route`` 节点（vendor 的 ``OP_TO_NODE`` 一行不改）。

跑法：
    python -m unittest tests.test_state_route -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.workflow_service import state_route as sr  # noqa: E402

QUANTUM_BASE = "http://127.0.0.1:3003"


class FakeNode:
    def __init__(self, config=None, depends=None, node_id="d1"):
        self.id = node_id
        self.type = sr.STATE_ROUTE_NODE
        self.config = dict(config or {})
        self.depends = list(depends or [])


class FakeService:
    """只提供 state_route 执行器用到的那点接口。"""

    def __init__(self, base=QUANTUM_BASE, variables=None):
        self._quantum_service_url = base
        self._variables = dict(variables or {})

    def _resolve_vars(self, value, context):
        if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
            name = value[2:-2].strip()
            found = (context or {}).get(name)
            if found:
                return str(found)
        return value


def _route_accept(fn="sq.spectroscopy", edge="unknown->freq_locked", **extra):
    payload = {
        "success": True,
        "name": "q0",
        "state": {"state": "unknown"},
        "decision": "accept",
        "branch": "G1",
        "current_state": "unknown",
        "allowed_edges": [edge],
        "chosen_action": {"fn": fn, "params": {}, "edge_id": edge, "template_id": "T1"},
        "reason": "模板 T1 命中",
        "confidence": 0.9,
        "template": "T1",
        "needs_human": False,
        "gates": [{"gate": "GATE-PRE", "passed": True}],
    }
    payload.update(extra)
    return payload


def _route_ask(edge="unknown->freq_locked", fn="sq.spectroscopy"):
    return {
        "success": True,
        "name": "q0",
        "decision": "ask_llm",
        "branch": "G3",
        "current_state": "unknown",
        "allowed_edges": [edge],
        "chosen_action": None,
        "reason": "合法边存在但模板库里没有适用写法",
        "confidence": 0.5,
        "needs_human": False,
        "llm_task": {
            "allowed_edges": [edge],
            "allowed_actions": [{"fn": fn, "edge_id": edge, "intent": "锁频"}],
            "instruction": "只在 allowed_edges 内选一个动作，产出必须落成 qwf",
        },
    }


class _PatchMixin:
    """把 `_post_json` / LLM 入口换成桩，并记录调用。"""

    def _patch_route(self, response=None, error=None):
        calls = []
        original = sr._post_json

        def fake(url, payload, timeout=60.0):
            calls.append({"url": url, "payload": payload, "timeout": timeout})
            if error is not None:
                raise error
            return response

        sr._post_json = fake
        self.addCleanup(lambda: setattr(sr, "_post_json", original))
        return calls

    def _patch_llm(self, content="", error="", configured=True):
        calls = []
        original = sr._llm_config_and_chat

        def fake_entry():
            def load_config(_name):
                if not configured:
                    return {}
                return {"api_key": "k", "base_url": "http://llm", "model": "m"}

            def chat(cfg, messages, temperature=0.3, max_tokens=1200):
                calls.append({"messages": messages, "temperature": temperature})
                return content, error

            return load_config, chat

        sr._llm_config_and_chat = fake_entry
        self.addCleanup(lambda: setattr(sr, "_llm_config_and_chat", original))
        return calls


class TestNormalizeActions(unittest.TestCase):
    def test_accepts_plain_strings(self):
        self.assertEqual(
            sr._normalize_actions(["sq.piamp", " sq.t1 "]),
            [{"fn": "sq.piamp"}, {"fn": "sq.t1"}],
        )

    def test_string_with_arrow_is_edge_id(self):
        self.assertEqual(
            sr._normalize_actions(["unknown->freq_locked"]),
            [{"edge_id": "unknown->freq_locked"}],
        )

    def test_accepts_dict_forms(self):
        self.assertEqual(
            sr._normalize_actions([
                {"fn": "sq.piamp", "params": {"amp": 0.3}, "intent": "标定"},
                {"edge": "a->b"},
                {"nope": 1},
            ]),
            [
                {"fn": "sq.piamp", "params": {"amp": 0.3}, "intent": "标定"},
                {"edge_id": "a->b"},
            ],
        )

    def test_empty(self):
        self.assertEqual(sr._normalize_actions(None), [])
        self.assertEqual(sr._normalize_actions([]), [])


class TestAccept(_PatchMixin, unittest.TestCase):
    def test_g1_accept_returns_action_without_llm(self):
        calls = self._patch_route(_route_accept())
        llm_calls = self._patch_llm(content="should not be called")
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["decision"], "accept")
        self.assertEqual(result["branch"], "G1")
        self.assertEqual(result["action"]["fn"], "sq.spectroscopy")
        self.assertEqual(result["action"]["edge_id"], "unknown->freq_locked")
        self.assertEqual(result["source"], "template")
        self.assertEqual(llm_calls, [], "G1 不许问 LLM")
        # 请求体：只有 name，没有显式 actions → 让量子服务按边表生成候选
        self.assertEqual(calls[0]["url"], f"{QUANTUM_BASE}/qubit/route")
        self.assertEqual(calls[0]["payload"], {"name": "q0"})

    def test_g2_accept_marks_param_refresh(self):
        self._patch_route(_route_accept(branch="G2", requires_param_refresh=True))
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["requires_param_refresh"])

    def test_explicit_actions_are_forwarded(self):
        calls = self._patch_route(_route_accept())
        node = FakeNode({"qubit": "q0", "actions": ["sq.piamp"]})
        sr.execute_state_route(FakeService(), node)
        self.assertEqual(calls[0]["payload"]["actions"], [{"fn": "sq.piamp"}])

    def test_choices_used_as_actions(self):
        """IR 侧写的是 `choices [...]`，落成节点 config 后要当候选动作转发。"""
        calls = self._patch_route(_route_accept())
        node = FakeNode({"qubit": "q0", "choices": ["sq.iqraw"]})
        sr.execute_state_route(FakeService(), node)
        self.assertEqual(calls[0]["payload"]["actions"], [{"fn": "sq.iqraw"}])

    def test_read_only_accept_surfaces_action_kind(self):
        """只读动作的 accept 免授权：节点原样采用，并把 action_kind 透出去。"""
        self._patch_route(
            _route_accept(
                fn="sq.t1",
                edge="state_discriminated->coherence_measured",
                action_kind="read",
                authorization={"kind": "read", "needs_any": [], "missing": [],
                               "satisfied": True, "read_only": True},
            )
        )
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["action"]["fn"], "sq.t1")
        self.assertEqual(result["action_kind"], "read")
        self.assertFalse(result["requires_param_refresh"])
        self.assertEqual(result["route"]["action_kind"], "read")
        self.assertIn("read", result["stdout"])

    def test_write_accept_without_gate_is_needs_human(self):
        """写参数动作在闸门关闭时 Router 给 hold，节点必须转人工而不是执行。"""
        self._patch_route({
            "success": True,
            "name": "q0",
            "decision": "hold",
            "branch": "G4",
            "current_state": "freq_locked",
            "allowed_edges": ["freq_locked->amp_calibrated"],
            "chosen_action": None,
            "reason": "该动作是 write，需要 allow_set_params；当前 allow_set_params 未打开",
            "confidence": 0.9,
            "needs_human": True,
            "requires_authorization": True,
            "gates": [{"gate": "GATE-PRE", "passed": True}],
        })
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")
        self.assertTrue(result["needs_human"])
        self.assertEqual(result["branch"], "G4")
        self.assertTrue(result["requires_authorization"])
        self.assertIsNone(result.get("action"))

    def test_accept_without_chosen_action_fails(self):
        self._patch_route(_route_accept(chosen_action=None))
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "failed")
        self.assertIn("chosen_action", result["error"])


class TestAskLlm(_PatchMixin, unittest.TestCase):
    def test_in_bounds_llm_answer_is_used(self):
        self._patch_route(_route_ask())
        calls = self._patch_llm(content='{"fn": "sq.spectroscopy", "reasoning": "锁频"}')
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["branch"], "G3")
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["action"], {
            "fn": "sq.spectroscopy",
            "params": {},
            "edge_id": "unknown->freq_locked",
            "intent": "锁频",
        })
        # 约束必须真的写进 prompt（LLM 只能看到合法边）
        system_prompt = calls[0]["messages"][0]["content"]
        self.assertIn("unknown->freq_locked", system_prompt)
        self.assertIn("Allowed actions", system_prompt)

    def test_out_of_bounds_llm_answer_is_rejected(self):
        self._patch_route(_route_ask())
        self._patch_llm(content='{"fn": "sq.xeb", "reasoning": "越界"}')
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")
        self.assertTrue(result["needs_human"])
        self.assertNotIn("action", result)
        violations = result["violations"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["fn"], "sq.xeb")
        self.assertEqual(violations[0]["violation"], "action_not_in_allowed_edges")

    def test_recommendations_fall_back_to_next_allowed(self):
        self._patch_route(_route_ask())
        self._patch_llm(content=(
            '{"recommendations": [{"fn": "sq.t1", "reason": "越界"}, '
            '{"fn": "sq.spectroscopy", "params": {"freq": 5.0}, "reason": "合法"}]}'
        ))
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["action"]["fn"], "sq.spectroscopy")
        self.assertEqual(result["action"]["params"], {"freq": 5.0})
        self.assertEqual(result["metrics"]["violation_count"], 1)

    def test_garbage_llm_output_goes_to_human(self):
        self._patch_route(_route_ask())
        self._patch_llm(content="我觉得应该先锁频……")
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")

    def test_llm_error_goes_to_human(self):
        self._patch_route(_route_ask())
        self._patch_llm(content="", error="timeout")
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")
        self.assertIn("timeout", result["message"])

    def test_unconfigured_llm_goes_to_human(self):
        self._patch_route(_route_ask())
        self._patch_llm(content='{"fn": "sq.spectroscopy"}', configured=False)
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")
        self.assertIn("llm_decision", result["message"])

    def test_empty_allowed_actions_goes_to_human(self):
        route = _route_ask()
        route["llm_task"]["allowed_actions"] = []
        self._patch_route(route)
        self._patch_llm(content='{"fn": "sq.spectroscopy"}')
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")


class TestNeedsHumanDecisions(_PatchMixin, unittest.TestCase):
    def test_g0_reject_carries_missing(self):
        self._patch_route({
            "success": True, "name": "q0", "decision": "reject", "branch": "G0",
            "current_state": "unknown", "allowed_edges": [],
            "missing": {"states": ["freq_locked"], "evidence": ["freq"], "edges": []},
            "reason": "候选动作都不在当前态的合法边集合 A(state) 内",
        })
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")
        self.assertTrue(result["needs_human"])
        self.assertEqual(result["missing"]["states"], ["freq_locked"])
        self.assertIn("freq_locked", result["question"])

    def test_g4_hold_carries_authorization(self):
        self._patch_route({
            "success": True, "name": "q0", "decision": "hold", "branch": "G4",
            "current_state": "unknown", "allowed_edges": ["unknown->freq_locked"],
            "needs_human": True, "requires_authorization": True,
            "reason": "写授权关闭（allow_execute / allow_set_params 均未打开），需人工授权后重判",
        })
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "needs_human")
        self.assertTrue(result["requires_authorization"])
        self.assertIn("写授权关闭", result["message"])


class TestFailures(_PatchMixin, unittest.TestCase):
    def test_route_unavailable_fails_without_action(self):
        self._patch_route(error=ConnectionError("connection refused"))
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["route_unavailable"])
        self.assertNotIn("action", result)

    def test_success_false_is_failure(self):
        self._patch_route({"success": False, "error": "validation_error: name is required"})
        result = sr.execute_state_route(FakeService(), FakeNode({"qubit": "q0"}))
        self.assertEqual(result["status"], "failed")
        self.assertIn("validation_error", result["error"])

    def test_missing_qubit_fails(self):
        calls = self._patch_route(_route_accept())
        result = sr.execute_state_route(FakeService(), FakeNode({}))
        self.assertEqual(result["status"], "failed")
        self.assertIn("qubit", result["error"])
        self.assertEqual(calls, [], "缺比特时一个请求都不该发")

    def test_qubit_falls_back_to_upstream_result(self):
        calls = self._patch_route(_route_accept())
        node = FakeNode({}, depends=["s1"])
        result = sr.execute_state_route(FakeService(), node, {"s1": {"qubit": "q3"}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls[0]["payload"]["name"], "q3")

    def test_qubit_variable_is_resolved(self):
        calls = self._patch_route(_route_accept())
        node = FakeNode({"qubit": "{{target}}"})
        sr.execute_state_route(FakeService(), node, {"target": "q7"})
        self.assertEqual(calls[0]["payload"]["name"], "q7")


class TestInstall(unittest.TestCase):
    def test_install_registers_builtin_executor(self):
        class Svc:
            def __init__(self):
                self._builtin_executors = {}

        svc = Svc()
        sr.install(svc)
        self.assertIn(sr.STATE_ROUTE_NODE, svc._builtin_executors)
        self.assertTrue(callable(svc._builtin_executors[sr.STATE_ROUTE_NODE]))

    def test_install_is_idempotent(self):
        class Svc:
            def __init__(self):
                self._builtin_executors = {}

        svc = Svc()
        sr.install(svc)
        first = svc._builtin_executors[sr.STATE_ROUTE_NODE]
        sr.install(svc)
        self.assertNotEqual(first, svc._builtin_executors[sr.STATE_ROUTE_NODE])

    def test_install_without_registry_raises(self):
        with self.assertRaises(AttributeError):
            sr.install(object())


class _IrSvc:
    """`IrExecutor.prepare()` 需要的最小 service 面。"""

    def __init__(self):
        self._workflows = {}
        self._cancel_events = {}


def _decision_ir(mode=None, choices=None):
    stmt = {"op": "decide", "as": "d1"}
    if mode is not None:
        stmt["mode"] = mode
    stmt["qubit"] = "$env.qubit"
    if choices is not None:
        stmt["choices"] = choices
    return {
        "ir_version": 1,
        "id": "wf_state_route_test",
        "env": {"qubit": "q0"},
        "pipeline": [stmt],
    }


STATE_ROUTE_QWF = """
workflow route_demo {
  env { qubit "q0" }
  decide "lock the qubit" as d1 {
    mode "state_route"
    qubit "$env.qubit"
    choices ["sq.spectroscopy", "sq.piamp"]
  }
}
"""


class TestIrLanding(unittest.TestCase):
    """IR 落点：vendor 的 OP_TO_NODE 不改，靠本仓子类改写节点类型。"""

    @staticmethod
    def _validator():
        """取 `validate()`。

        坑：只要有人 `import services.workflow_service.dsl.validate`（`test_multiqubit_ir.py`
        就这么干），包属性 `dsl.validate` 就会被改成**子模块**，`from .dsl import validate`
        拿到的是 module 而不是函数 —— 结果跟测试顺序有关。这里两种形态都兼容。
        """
        from services.workflow_service import dsl as dsl_mod

        fn = getattr(dsl_mod, "validate", None)
        if callable(fn):
            return fn
        from services.workflow_service.dsl.validate import validate as _validate

        return _validate

    def _prepare(self, ir):
        dsl_validate = self._validator()

        normalized, diags = dsl_validate(ir, require_writes=False)
        errors = [d for d in diags if d.get("level") == "error"]
        self.assertEqual(errors, [], [d.get("message") for d in errors])
        svc = _IrSvc()
        executor = sr.QmclawIrExecutor(svc, normalized, context={}, workflow_id="wf_t")
        executor.prepare()
        return svc._workflows["wf_t"], executor

    def test_state_route_mode_becomes_state_route_node(self):
        workflow, _executor = self._prepare(_decision_ir("state_route", ["sq.spectroscopy"]))
        self.assertEqual(workflow.nodes["d1"].type, sr.STATE_ROUTE_NODE)

    def test_plain_decide_stays_decision(self):
        workflow, _executor = self._prepare(_decision_ir(None, ["a", "b"]))
        self.assertEqual(workflow.nodes["d1"].type, "decision")

    def test_other_modes_stay_decision(self):
        for mode in ("analysis", "intent"):
            workflow, _executor = self._prepare(_decision_ir(mode, ["a"]))
            self.assertEqual(workflow.nodes["d1"].type, "decision", mode)

    def test_qubit_is_carried_into_config_before_dispatch(self):
        _workflow, executor = self._prepare(_decision_ir("state_route", ["sq.spectroscopy"]))
        stmt = executor._sr_index()["d1"]
        self.assertEqual(stmt["mode"], "state_route")

    def test_text_dsl_authoring_path(self):
        """用户视角：写 .qwf 时 `decide ... { mode "state_route" }` 就能落到新节点。"""
        from services.workflow_service.dsl.textdsl import parse

        ir = parse(STATE_ROUTE_QWF)
        self.assertEqual(ir["pipeline"][0]["mode"], "state_route")
        workflow, _executor = self._prepare(ir)
        self.assertEqual(workflow.nodes["d1"].type, sr.STATE_ROUTE_NODE)

    def test_is_state_route_stmt(self):
        self.assertTrue(sr.QmclawIrExecutor._is_state_route_stmt(
            {"op": "decide", "mode": "state_route"}))
        self.assertFalse(sr.QmclawIrExecutor._is_state_route_stmt(
            {"op": "decide", "mode": "analysis"}))
        self.assertFalse(sr.QmclawIrExecutor._is_state_route_stmt(
            {"op": "measure", "mode": "state_route"}))
        self.assertFalse(sr.QmclawIrExecutor._is_state_route_stmt(None))

    def test_dispatch_keeps_type_and_merges_qubit(self):
        """`_ensure_node` 是真正下发前的钩子：类型要改回 state_route，qubit 要补上。

        compile 阶段 decide 的 config 里没有 qubit（decision 节点用不到它），
        如果在 `_ensure_node` 里不补，执行时就会"缺 qubit"直接失败。
        """
        workflow, executor = self._prepare(_decision_ir("state_route", ["sq.spectroscopy"]))
        node = executor._ensure_node("d1", "decision", {"mode": "state_route"}, [])
        self.assertIs(node, workflow.nodes["d1"])
        self.assertEqual(node.type, sr.STATE_ROUTE_NODE)
        self.assertEqual(node.config.get("qubit"), "q0")

    def test_end_to_end_via_builtin_executor(self):
        """prepare → _ensure_node → install 的执行器 = 一条完整的判定链。"""
        workflow, executor = self._prepare(_decision_ir("state_route", ["sq.spectroscopy"]))

        class Svc(_IrSvc, FakeService):
            def __init__(self):
                _IrSvc.__init__(self)
                FakeService.__init__(self)
                self._builtin_executors = {}

        svc = Svc()
        sr.install(svc)
        original = sr._post_json
        calls = []

        def fake(url, payload, timeout=60.0):
            calls.append(payload)
            return _route_accept()

        sr._post_json = fake
        self.addCleanup(lambda: setattr(sr, "_post_json", original))
        node = executor._ensure_node(
            "d1", "decision", {"mode": "state_route", "choices": ["sq.spectroscopy"]}, [])
        result = svc._builtin_executors[sr.STATE_ROUTE_NODE](node, {})
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["action"]["fn"], "sq.spectroscopy")
        # qubit 来自 IR 的 `$env.qubit`，actions 来自 choices
        self.assertEqual(calls[0], {"name": "q0", "actions": [{"fn": "sq.spectroscopy"}]})


if __name__ == "__main__":
    unittest.main()

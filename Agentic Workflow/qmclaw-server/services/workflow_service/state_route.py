"""`state_route` 节点：把状态机 Router 接进运行时决策链。

设计稿（``docs/STATE_MACHINE_DESIGN.md`` §7.6）写下的目标链路是：

    候选生成 → ``route()`` 唯一准入判定 → 仅 G3 时回头调 LLM（限在 allowed_edges 内）
    → 落成可执行动作 → 执行层

现状：``decision`` 节点是 **LLM 直连**（模型自己决定下一步做什么），与「结论只能
程序下」相悖。本模块新增一个**独立节点类型** ``state_route``：

* ``accept``（G1 模板命中且稳定 / G2 模板命中但参数漂移）—— 直接用 Router 给的动作，
  **不问 LLM**；
* ``ask_llm``（G3 合法边存在但模板库里没有适用写法）—— 才去调 LLM，并且把
  ``allowed_edges`` 写进 system prompt；模型给出的 ``fn`` 若越界（不在候选内），
  **丢弃该建议并转 needs_human**，绝不放行越界动作；
* ``reject``（G0 候选动作不在 A(s) 内）/ ``hold``（G4 越权、连续失败、自环用尽）——
  直接转 needs_human，并把 ``missing``（还缺哪些证据）带上去。

``decision`` 节点一行不改：零回归风险，可以逐条流程灰度接入。

为什么还要 ``QmclawIrExecutor``
-------------------------------
IR 是 **op-based** 的，``decide`` op → 节点 type ``decision`` 的映射表（``OP_TO_NODE``）
住在 ``vendor/openqwf/const.py``，而 vendor 是「只读上游副本」这条红线不许改。
于是本仓在**部署侧**继承上游 ``IrExecutor``，只覆写两个小钩子：

1. :meth:`QmclawIrExecutor._node_type` —— ``prepare()`` 预建节点视图时，把
   ``mode: "state_route"`` 的 decide 语句认成 ``state_route``；
2. :meth:`QmclawIrExecutor._ensure_node` —— 真正下发前同样改写类型，并补回
   compile 阶段丢掉的字段（``qubit``）。

DSL 语法不变，写 ``decide ... mode "state_route" qubit "q0" choices ["sq.piamp"]``
就落到这里。``choices`` 在这个节点里的语义是**候选动作的 fn 列表**（缺省则由
quantum_service 按当前态的边表自动生成候选）。

节点结果结构
------------
* 成功（G1/G2 或 G3 通过校验）::

      {"status": "completed", "decision": "accept"|"ask_llm", "branch": "G1"|"G2"|"G3",
       "state": "unknown", "action": {"fn": "sq.spectroscopy", "params": {}, "edge_id": "..."},
       "allowed_edges": [...], "source": "template"|"llm", "route": {...摘要...}}

* 需要人工（G0/G4、LLM 越界、LLM 未配置）::

      {"status": "needs_human", "needs_human": True, "decision": ..., "branch": ...,
       "missing": {...}, "violations": [...], "question": "...", "route": {...}}

* 路由服务不可用 / 缺 qubit：``{"status": "failed", "error": ...}``（不下发任何动作）。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional

#: 节点类型名（IR 侧通过 `decide ... mode "state_route"` 触发）
STATE_ROUTE_NODE = "state_route"

#: 默认超时（秒）：路由是纯计算，正常几十毫秒；给 60s 是怕 quantum_service 正在
#: 跑一次大台账扫描（`list_records` 读几百条 RunRecord）。
DEFAULT_ROUTE_TIMEOUT = 60.0


def _log(msg: str) -> None:
    """带前缀的日志（与 server.py 的 ``_log`` 同前缀，方便按服务筛）。"""
    try:
        print(f"[workflow_service] [state_route] {msg}", flush=True)
    except Exception:  # pragma: no cover - 日志永远不该让流程失败
        pass


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _post_json(url: str, payload: Dict[str, Any], timeout: float = DEFAULT_ROUTE_TIMEOUT) -> Dict[str, Any]:
    """POST 一个 JSON 并解析 JSON 响应。

    单独抽出来是为了**可测**：单测直接替换本函数，不用真起 quantum_service。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"路由服务返回非 JSON（{exc}）：{raw[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"路由服务返回的不是对象：{raw[:200]!r}")
    return parsed


# ── 入参归一 ──────────────────────────────────────────────────────────────────
def _normalize_actions(raw: Any) -> List[Dict[str, Any]]:
    """把 config 里的候选动作归一成 ``[{fn|edge_id, params, intent}]``。

    接受三种写法：``"sq.piamp"`` / ``{"fn": "sq.piamp"}`` / ``{"edge_id": "..."}``。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"fn": text} if not text.count("->") else {"edge_id": text})
            continue
        if not isinstance(item, dict):
            continue
        action: Dict[str, Any] = {}
        fn = item.get("fn") or item.get("function")
        edge_id = item.get("edge_id") or item.get("edge")
        if fn:
            action["fn"] = str(fn).strip()
        if edge_id:
            action["edge_id"] = str(edge_id).strip()
        params = item.get("params")
        if isinstance(params, dict) and params:
            action["params"] = params
        intent = item.get("intent")
        if intent:
            action["intent"] = str(intent)
        if action:
            out.append(action)
    return out


def _resolve_qubit(service: Any, node: Any, config: Dict[str, Any],
                   context: Dict[str, Any]) -> str:
    """比特来源：config.qubit（支持 ``{{ref}}``） → 上游节点结果 → 空。"""
    raw = config.get("qubit") or config.get("qubits")
    resolve = getattr(service, "_resolve_vars", None)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ""
    if isinstance(raw, str) and raw:
        value = raw
        if callable(resolve):
            try:
                value = str(resolve(raw, context) or "")
            except Exception:
                value = raw
        else:
            value = raw.replace("{{", "").replace("}}", "")
        if value and "{{" not in value:
            return value
    for dep_id in getattr(node, "depends", None) or []:
        dep = context.get(dep_id) or {}
        if isinstance(dep, dict):
            qubit = dep.get("qubit") or dep.get("target")
            if qubit:
                return str(qubit)
    return ""


def _route_summary(route: Dict[str, Any]) -> Dict[str, Any]:
    """只留可观测字段，避免把整个 candidates 列表塞进节点结果。"""
    keep = ("decision", "branch", "current_state", "allowed_edges", "needs_human",
            "confidence", "reason", "missing", "requires_param_refresh",
            "requires_authorization", "blocked_by", "template")
    return {key: route.get(key) for key in keep if key in route}


def _render_stdout(route: Dict[str, Any], extra: str = "") -> str:
    lines = [
        f"[STATE_ROUTE] decision={route.get('decision')} branch={route.get('branch')}",
        f"[STATE_ROUTE] state={route.get('current_state')} "
        f"allowed_edges={json.dumps(route.get('allowed_edges') or [], ensure_ascii=False)}",
    ]
    reason = str(route.get("reason") or "")
    if reason:
        lines.append(f"[STATE_ROUTE] reason={reason}")
    if extra:
        lines.append(f"[STATE_ROUTE] {extra}")
    return "\n".join(lines)


# ── G3：LLM 只能在合法边内选 ──────────────────────────────────────────────────
def _llm_config_and_chat():
    """取（延迟导入）LLM 配置读取器与 chat 调用。

    单独抽成函数是给单测留的替换点：`state_route._llm_config_and_chat = ...`。
    """
    from .dsl.llm import chat, load_llm_config

    return load_llm_config, chat


def _allowed_action_list(route: Dict[str, Any]) -> List[Dict[str, Any]]:
    task = route.get("llm_task") or {}
    actions = task.get("allowed_actions")
    if isinstance(actions, list) and actions:
        return [a for a in actions if isinstance(a, dict) and a.get("fn")]
    return []


def _parse_llm_json(content: str) -> Dict[str, Any]:
    """模型常带 markdown 围栏或前言后语，这里做宽容解析。"""
    text = (content or "").strip()
    if not text:
        return {}
    import re as _re

    fence = _re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start:end + 1])
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _ask_llm(service: Any, config: Dict[str, Any], route: Dict[str, Any],
             qubit: str) -> Dict[str, Any]:
    """G3：把 Router 给的合法边交给 LLM，**只接受落在候选内的 fn**。"""
    allowed = _allowed_action_list(route)
    allowed_fns = [str(a.get("fn")) for a in allowed]
    if not allowed_fns:
        # 没有候选可给 → 不是 LLM 能解决的问题，直接转人工
        return {
            "status": "needs_human",
            "needs_human": True,
            "decision": route.get("decision"),
            "branch": route.get("branch"),
            "state": route.get("current_state"),
            "allowed_edges": route.get("allowed_edges"),
            "question": "路由判定要 LLM 在合法边内选动作，但没有可用的候选动作，需人工确认",
            "message": "G3 缺少候选动作",
            "route": _route_summary(route),
            "stdout": _render_stdout(route, "llm_task.allowed_actions 为空"),
        }

    try:
        load_llm_config, chat = _llm_config_and_chat()
        llm_cfg = load_llm_config("llm_decision") or {}
    except Exception as exc:
        return {
            "status": "needs_human",
            "needs_human": True,
            "decision": route.get("decision"),
            "branch": route.get("branch"),
            "state": route.get("current_state"),
            "allowed_edges": route.get("allowed_edges"),
            "question": f"G3 需要 LLM 选动作，但 LLM 模块不可用（{exc}），需人工确认",
            "message": "LLM 模块不可用",
            "route": _route_summary(route),
            "stdout": _render_stdout(route, f"llm 不可用：{exc}"),
        }

    api_key = str(llm_cfg.get("api_key") or "")
    base_url = str(llm_cfg.get("base_url") or "")
    if not api_key or not base_url:
        return {
            "status": "needs_human",
            "needs_human": True,
            "decision": route.get("decision"),
            "branch": route.get("branch"),
            "state": route.get("current_state"),
            "allowed_edges": route.get("allowed_edges"),
            "question": "G3 需要 LLM 选动作，但 config/qubitclient.json 的 llm_decision 未配置，需人工确认",
            "message": "llm_decision 未配置",
            "route": _route_summary(route),
            "stdout": _render_stdout(route, "llm_decision 未配置（缺 api_key/base_url）"),
        }

    system_prompt = str(config.get("systemPrompt") or "").strip()
    if not system_prompt:
        system_prompt = (
            "You are an expert superconducting-qubit calibration assistant. "
            "The state machine has already decided WHICH transitions are legal. "
            "You may only choose one action from the allowed list; anything else is rejected."
        )
    # 硬约束写进 prompt：合法边 + 候选 fn（越界的建议会被丢弃并转人工）
    system_prompt = (
        system_prompt.rstrip()
        + "\n\nAllowed edges (you must stay inside): "
        + json.dumps(route.get("allowed_edges") or [], ensure_ascii=False)
        + "\nAllowed actions (choose exactly one fn from this list): "
        + json.dumps(allowed, ensure_ascii=False)
        + '\nRespond ONLY with JSON: {"fn": "<one allowed fn>", "params": {}, '
          '"reasoning": "<why this action>"}'
    )

    intent = str(config.get("intentPrompt") or "").strip()
    user_parts = [
        f"Qubit: {qubit}",
        f"Current state: {route.get('current_state')}",
        f"Allowed edges: {json.dumps(route.get('allowed_edges') or [], ensure_ascii=False)}",
        f"Allowed actions: {json.dumps(allowed, ensure_ascii=False)}",
    ]
    if intent:
        user_parts.append(f"User intent: {intent}")
    user_parts.append("Choose exactly one allowed action and justify it.")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]

    temperature = float(config.get("temperature", 0.3) or 0.3)
    max_tokens = int(config.get("maxTokens", 1200) or 1200)
    try:
        content, llm_err = chat(llm_cfg, messages, temperature=temperature, max_tokens=max_tokens)
    except Exception as exc:
        content, llm_err = "", str(exc)
    if not content:
        return {
            "status": "needs_human",
            "needs_human": True,
            "decision": route.get("decision"),
            "branch": route.get("branch"),
            "state": route.get("current_state"),
            "allowed_edges": route.get("allowed_edges"),
            "question": f"G3 的 LLM 调用失败（{llm_err}），需人工确认下一步",
            "message": f"LLM 调用失败：{llm_err}",
            "route": _route_summary(route),
            "stdout": _render_stdout(route, f"LLM 调用失败：{llm_err}"),
        }

    parsed = _parse_llm_json(content)
    fn = str(parsed.get("fn") or parsed.get("choice") or "").strip()
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    reasoning = str(parsed.get("reasoning") or parsed.get("reason") or "")

    # 模型可能只给 choice（字符串）或给 recommendations 列表：都收一遍
    candidates: List[Dict[str, Any]] = []
    if fn:
        candidates.append({"fn": fn, "params": params, "reasoning": reasoning})
    for rec in parsed.get("recommendations") or []:
        if isinstance(rec, dict) and rec.get("fn"):
            candidates.append({
                "fn": str(rec.get("fn")),
                "params": rec.get("params") if isinstance(rec.get("params"), dict) else {},
                "reasoning": str(rec.get("reason") or ""),
            })

    violations: List[Dict[str, Any]] = []
    chosen: Optional[Dict[str, Any]] = None
    for cand in candidates:
        if cand["fn"] in allowed_fns:
            matched = next(a for a in allowed if str(a.get("fn")) == cand["fn"])
            chosen = {
                "fn": cand["fn"],
                "params": cand["params"],
                "edge_id": matched.get("edge_id"),
                "intent": matched.get("intent"),
            }
            break
        violations.append({
            "fn": cand["fn"],
            "violation": "action_not_in_allowed_edges",
            "detail": "LLM 的建议不在 allowed_actions 内（也许来自别的状态），已丢弃",
        })

    if chosen is None:
        detail = "；".join(f"{v['fn']}（{v['violation']}）" for v in violations) or "模型没有给出可用的 fn"
        return {
            "status": "needs_human",
            "needs_human": True,
            "decision": route.get("decision"),
            "branch": route.get("branch"),
            "state": route.get("current_state"),
            "allowed_edges": route.get("allowed_edges"),
            "allowed_actions": allowed,
            "violations": violations,
            "llm_response": content[:2000],
            "question": f"LLM 给出的动作越界或无法解析（{detail}），需人工确认",
            "message": "LLM 越界即拒绝",
            "route": _route_summary(route),
            "stdout": _render_stdout(route, f"LLM 越界/无效：{detail}"),
        }

    return {
        "status": "completed",
        "decision": route.get("decision"),
        "branch": route.get("branch"),
        "state": route.get("current_state"),
        "action": chosen,
        "allowed_edges": route.get("allowed_edges"),
        "allowed_actions": allowed,
        "source": "llm",
        "reasoning": reasoning,
        "violations": violations,
        "route": _route_summary(route),
        "metrics": {"violation_count": len(violations)},
        "stdout": _render_stdout(route, f"LLM 选择 {chosen['fn']}（{chosen.get('edge_id')}）"),
    }


# ── 节点执行主体 ──────────────────────────────────────────────────────────────
def execute_state_route(service: Any, node: Any, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """执行一个 ``state_route`` 节点（纯判定 + 可能的 LLM 咨询，**不下发硬件**）。"""
    context = context or {}
    config = getattr(node, "config", None) or {}
    qubit = _resolve_qubit(service, node, config, context)
    if not qubit:
        return {
            "status": "failed",
            "error": "state_route 节点缺少 qubit（config.qubit 与上游节点结果里都没有）",
        }

    actions = _normalize_actions(config.get("actions") or config.get("choices"))
    payload: Dict[str, Any] = {"name": qubit}
    if actions:
        payload["actions"] = actions

    base = str(getattr(service, "_quantum_service_url", "") or "http://localhost:3003").rstrip("/")
    url = f"{base}/qubit/route"
    timeout = float(config.get("routeTimeout") or DEFAULT_ROUTE_TIMEOUT)

    _log(f"node={getattr(node, 'id', '?')} qubit={qubit} actions={len(actions)} → {url}")
    try:
        route = _post_json(url, payload, timeout=timeout)
    except Exception as exc:
        # 路由服务不可用 = 判不了 → 失败（不下发任何动作）。这比"猜一个"安全得多。
        _log(f"route unavailable: {exc}")
        return {
            "status": "failed",
            "route_unavailable": True,
            "error": f"状态机路由服务不可用（{url}）：{exc}",
        }

    if route.get("success") is False:
        error = str(route.get("error") or "路由请求被拒绝")
        _log(f"route rejected: {error}")
        return {"status": "failed", "error": f"状态机路由请求被拒绝：{error}", "route": route}

    decision = str(route.get("decision") or "")
    branch = str(route.get("branch") or "")
    allowed_edges = list(route.get("allowed_edges") or [])

    if decision == "accept":
        action = dict(route.get("chosen_action") or {})
        if not action.get("fn") and not action.get("edge_id"):
            return {
                "status": "failed",
                "error": f"路由返回 accept 但没有 chosen_action（branch={branch}）",
                "route": _route_summary(route),
            }
        source = "template" if route.get("template") else "router"
        return {
            "status": "completed",
            "decision": decision,
            "branch": branch,
            "state": route.get("current_state"),
            "action": action,
            "allowed_edges": allowed_edges,
            "source": source,
            "requires_param_refresh": bool(route.get("requires_param_refresh")),
            "confidence": route.get("confidence"),
            "route": _route_summary(route),
            "stdout": _render_stdout(
                route,
                f"{branch} 直接采用 {action.get('fn')}（{action.get('edge_id')}）"
                + ("，参数需重新标定" if route.get("requires_param_refresh") else ""),
            ),
        }

    if decision == "ask_llm":
        return _ask_llm(service, config, route, qubit)

    # reject（G0）/ hold（G4）/ 其它未知 decision → 一律转人工，绝不下发
    missing = route.get("missing")
    missing_text = ""
    if isinstance(missing, dict):
        states = missing.get("states") or []
        evidence = missing.get("evidence") or []
        if states or evidence:
            missing_text = (
                f"还缺：状态 {json.dumps(states, ensure_ascii=False)}"
                f" / 证据 {json.dumps(evidence, ensure_ascii=False)}"
            )
    reason = str(route.get("reason") or "")
    question = f"状态机路由判定 {decision or '未知'}/{branch or '?'}，需人工确认下一步"
    if missing_text:
        question = f"{question}（{missing_text}）"
    return {
        "status": "needs_human",
        "needs_human": True,
        "decision": decision,
        "branch": branch,
        "state": route.get("current_state"),
        "allowed_edges": allowed_edges,
        "missing": missing,
        "violations": route.get("violations"),
        "requires_authorization": bool(route.get("requires_authorization")),
        "reason": reason,
        "route": _route_summary(route),
        "question": question,
        "message": reason or question,
        "stdout": _render_stdout(route, missing_text or "转人工"),
    }


def install(service: Any) -> None:
    """把一个 WorkflowService 实例接上 ``state_route`` 执行器（幂等）。"""
    builtins = getattr(service, "_builtin_executors", None)
    if builtins is None:
        raise AttributeError("service 缺少 _builtin_executors，无法注册 state_route")

    def _executor(node: Any, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return execute_state_route(service, node, context)

    builtins[STATE_ROUTE_NODE] = _executor
    _log("executor installed")


# ── IR 侧落点：覆写 IrExecutor 的两个小钩子 ───────────────────────────────────
def _ir_executor_base():
    """延迟取上游 ``IrExecutor``（避免模块导入期就拉起整个 DSL 门面）。"""
    from .dsl import IrExecutor

    return IrExecutor


class QmclawIrExecutor(_ir_executor_base()):  # type: ignore[misc, valid-type]
    """本仓的 IR 执行器：只改「哪条语句落到哪个节点类型」这一件事。

    上游 ``OP_TO_NODE`` 把 ``decide`` 一律映射成 ``decision``；本仓需要
    ``decide ... mode "state_route"`` 落成 ``state_route``。红线下不能改 vendor，
    所以在部署侧继承 + 覆写下面两个钩子（其余执行逻辑一行不动）。
    """

    #: id(statement) 不会变，但 prepare() 里已经分配了 name，直接按 name 存
    _state_route_nodes: Optional[Dict[str, Dict[str, Any]]] = None

    def _sr_index(self) -> Dict[str, Dict[str, Any]]:
        index = getattr(self, "_state_route_nodes", None)
        if index is None:
            index = {}
            self._state_route_nodes = index
        return index

    @staticmethod
    def _is_state_route_stmt(stmt: Any) -> bool:
        return (
            isinstance(stmt, dict)
            and stmt.get("op") == "decide"
            and str(stmt.get("mode") or "").strip() == STATE_ROUTE_NODE
        )

    def _node_type(self, st):  # noqa: D401 - 覆写上游钩子
        base_type = super()._node_type(st)
        if self._is_state_route_stmt(st):
            name = self.node_names.get(id(st))
            if name:
                self._sr_index()[str(name)] = st
            return STATE_ROUTE_NODE
        return base_type

    def _ensure_node(self, name, node_type, config, depends):  # noqa: D401 - 覆写上游钩子
        stmt = self._sr_index().get(str(name))
        if stmt is not None:
            node_type = STATE_ROUTE_NODE
            config = dict(config or {})
            # compile 阶段不搬 `qubit`（decision 节点用不到），这里补回来
            if stmt.get("qubit") is not None and not config.get("qubit"):
                try:
                    config["qubit"] = self._resolve(stmt.get("qubit"))
                except Exception:
                    config["qubit"] = stmt.get("qubit")
        return super()._ensure_node(name, node_type, config, depends)

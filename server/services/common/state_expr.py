"""声明式状态判定：把「什么证据算达到某个状态」写成配置。

状态图原本硬编码在 `services/common/qubit_state.py`（红线文件）。为了让状态图
跟着芯片的调控难点变化，这里提供一套**声明式**表达 + 求值器，配置侧写在
`config/state_machine.json` 的 `states[*].when` 里，由 `machine_profile` 的能力
档案决定哪个 scope 启用、哪些边存在。

表达式只支持下面这些叶子（其余键一律报错，宁可写错就炸，不要静默放过）::

    {"all_of": [...]}                    全部成立
    {"any_of": [...]}                    任一成立
    {"none_of": [...]}                   全都不成立
    {"flag": "freq_locked"}              走 qubit_state 的 _flag 语义（bool 真 / 数字非零 / "1"|"true"|"yes"|"ok"|"locked"|"done"）
    {"evidence": "contrast", "is": true}                    按 flag 语义与给定布尔比较
    {"evidence": "contrast", "verdict_in": ["good"]}        判据 verdict 在名单内
    {"evidence": "contrast", "verdict_not_in": ["unusable"]} 判据 verdict 不在名单内
    {"evidence": "t1_us", "ge": 20, "le": 100}              原始数值比较（不换算单位）
    {"evidence": "contrast", "ok": true}                    判据 ok（阈值来自 lab_criteria.json）
    {"metric": "xeb", "ok": true}                           对 evidence["xeb"] 取判据
    {"prerequisite": "qubit>=state_discriminated"}          前置 scope 至少到某状态

设计约束：

* **纯函数**：不读文件、不连服务；criteria 由调用方传入（缺省才 `load_criteria()`）。
* **判据单一真源**：所有"过不过门槛"一律走 `services.common.metrics.evaluate`，
  本模块只做组合逻辑，不复制阈值。
* **与 legacy 对齐**：`derive_state_declarative` 的结果必须与
  `qubit_state.derive_state` 逐条一致（`tests/test_state_expr_parity.py` 守着这条），
  对齐之后才允许把 scope 的 `engine` 从 `legacy` 改成 `config`。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.common.metrics import evaluate, load_criteria

TRUE_STRINGS = ("1", "true", "yes", "ok", "locked", "done")

#: 证据键 -> 判据名。xeb 两个键共用一条判据（p / gate_fidelity 互相补位）。
EVIDENCE_METRICS: Dict[str, str] = {
    "contrast": "contrast",
    "dmu_sig": "dmu_sig",
    "t1_us": "t1",
    "t2_star_us": "t2_star",
    "xeb_p": "xeb",
    "xeb_gate_fidelity": "xeb",
    "cphase": "cphase",
    "cz_leakage": "cz_leakage",
    "cz_gate_fidelity": "cz_gate_fidelity",
}

#: 判据名 -> 参与 evaluate 的证据键（判据名与证据键并不总同名：t1 判据读 t1_us）。
METRIC_INPUTS: Dict[str, Tuple[str, ...]] = {
    "contrast": ("contrast",),
    "dmu_sig": ("dmu_sig",),
    "t1": ("t1_us",),
    "t2_star": ("t2_star_us",),
    "xeb": ("xeb_p", "xeb_gate_fidelity"),
    "cphase": ("cphase",),
    "cz_leakage": ("cz_leakage",),
    "cz_gate_fidelity": ("cz_gate_fidelity",),
}

GROUP_KEYS = ("all_of", "any_of", "none_of")
LEAF_KEYS = (
    "flag",
    "evidence",
    "metric",
    "prerequisite",
    "is",
    "verdict_in",
    "verdict_not_in",
    "ge",
    "le",
    "gt",
    "lt",
    "ok",
)

PREREQUISITE_STATES = ("unknown", "freq_locked", "amp_calibrated", "state_discriminated", "coherence_measured", "characterized")


def flag(evidence: Dict[str, Any], key: str) -> bool:
    """与 qubit_state._flag 完全一致的布尔语义（parity 测试守着）。"""
    val = (evidence or {}).get(key)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in TRUE_STRINGS
    return False


def has_evidence(evidence: Dict[str, Any], key: str) -> bool:
    return key in (evidence or {})


def _metric_value(metric: str, evidence: Dict[str, Any]) -> Any:
    """按判据名从证据里攒出 evaluate 需要的入参。"""
    evidence = evidence or {}
    keys = METRIC_INPUTS.get(metric)
    if not keys:
        return evidence.get(metric)
    if metric == "xeb":
        return {"p": evidence.get("xeb_p"), "gate_fidelity": evidence.get("xeb_gate_fidelity")}
    if len(keys) == 1:
        return evidence.get(keys[0])
    return tuple(evidence.get(k) for k in keys)


def evaluate_metric(
    metric: str,
    evidence: Dict[str, Any],
    criteria: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """对一条判据求值；证据缺失或判据未知时返回 None（而不是 False）。"""
    value = _metric_value(metric, evidence)
    if value is None:
        return None
    if isinstance(value, dict) and all(v is None for v in value.values()):
        return None
    try:
        return evaluate(metric, value, criteria)
    except (KeyError, TypeError, ValueError):
        # 判据表里没有这个指标（例如新 scope 还没配阈值）时按"未通过"处理，
        # 但不吞掉其它异常类型 —— 配置写错仍然要炸。
        return None


def evidence_metric(evidence: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    metric = EVIDENCE_METRICS.get(key, key)
    return evaluate_metric(metric, evidence)


def _parse_prerequisite(text: str) -> Tuple[str, str, str]:
    raw = str(text or "").strip()
    for op in (">=", "==", ">"):
        if op in raw:
            scope, _, state = raw.partition(op)
            return scope.strip(), op, state.strip()
    raise ValueError("prerequisite 表达式要写成 '<scope>>=<state>'，收到 %r" % (text,))


def _check_prerequisite(
    text: str,
    prerequisites: Optional[Dict[str, str]],
    order: Optional[Dict[str, Sequence[str]]],
    scope: Optional[str],
) -> bool:
    """前置 scope 是否至少到某状态。

    没有 `prerequisites` 信息时按"满足"处理（等价于老行为：单比特 scope 不关心
    别的 scope 在哪个状态），这样没配前置的调用方不会被新检查意外拦住。
    """
    if prerequisites is None:
        return True
    prereq_scope, op, state = _parse_prerequisite(text)
    actual = prerequisites.get(prereq_scope)
    if actual is None:
        return False
    if op == "==":
        return str(actual) == state
    order_map = (order or {}).get(prereq_scope)
    if not order_map:
        order_map = PREREQUISITE_STATES if prereq_scope == "qubit" else ()
    seq = list(order_map or ())
    if state not in seq or actual not in seq:
        return False
    return seq.index(actual) >= seq.index(state)


def eval_when(
    when: Any,
    evidence: Optional[Dict[str, Any]] = None,
    criteria: Optional[Dict[str, Any]] = None,
    *,
    prerequisites: Optional[Dict[str, str]] = None,
    order: Optional[Dict[str, Sequence[str]]] = None,
    scope: Optional[str] = None,
    trace: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """对一条 `when` 表达式求值。空表达式视为 True（"总是满足"）。"""
    evidence = evidence or {}
    if when is None:
        return True
    if isinstance(when, bool):
        return when
    if not isinstance(when, dict):
        raise ValueError("when 必须是对象，收到 %r" % (type(when).__name__,))

    def note(ok: bool, detail: Dict[str, Any]) -> bool:
        if trace is not None:
            trace.append({**detail, "ok": bool(ok)})
        return bool(ok)

    if "all_of" in when:
        parts = when.get("all_of") or []
        for part in parts:
            if not eval_when(part, evidence, criteria, prerequisites=prerequisites, order=order, scope=scope, trace=trace):
                return note(False, {"group": "all_of", "size": len(parts)})
        return note(True, {"group": "all_of", "size": len(parts)})

    if "any_of" in when:
        parts = when.get("any_of") or []
        sub: List[Dict[str, Any]] = []
        for part in parts:
            inner: List[Dict[str, Any]] = []
            ok = eval_when(part, evidence, criteria, prerequisites=prerequisites, order=order, scope=scope, trace=inner)
            sub.append({"ok": ok, "trace": inner})
            if ok:
                return note(True, {"group": "any_of", "size": len(parts)})
        return note(False, {"group": "any_of", "size": len(parts)})

    if "none_of" in when:
        parts = when.get("none_of") or []
        for part in parts:
            if eval_when(part, evidence, criteria, prerequisites=prerequisites, order=order, scope=scope, trace=trace):
                return note(False, {"group": "none_of", "size": len(parts)})
        return note(True, {"group": "none_of", "size": len(parts)})

    if "flag" in when:
        key = str(when["flag"])
        return note(flag(evidence, key), {"flag": key})

    if "prerequisite" in when:
        text = str(when["prerequisite"])
        ok = _check_prerequisite(text, prerequisites, order, scope)
        return note(ok, {"prerequisite": text})

    if "metric" in when:
        metric = str(when["metric"])
        want = bool(when.get("ok", True))
        ev = evaluate_metric(metric, evidence, criteria)
        ok = bool(ev and bool(ev.get("ok")) == want)
        return note(ok, {"metric": metric, "want_ok": want, "verdict": (ev or {}).get("verdict"), "present": ev is not None})

    if "evidence" in when:
        key = str(when["evidence"])
        if "is" in when:
            want = bool(when["is"])
            return note(flag(evidence, key) == want, {"evidence": key, "is": want})
        if "ge" in when or "le" in when or "gt" in when or "lt" in when:
            raw = evidence.get(key)
            if raw is None:
                return note(False, {"evidence": key, "compare": "missing"})
            ok = True
            if "ge" in when:
                ok = ok and float(raw) >= float(when["ge"])
            if "le" in when:
                ok = ok and float(raw) <= float(when["le"])
            if "gt" in when:
                ok = ok and float(raw) > float(when["gt"])
            if "lt" in when:
                ok = ok and float(raw) < float(when["lt"])
            return note(ok, {"evidence": key, "value": raw})
        ev = evidence_metric(evidence, key)
        verdict = (ev or {}).get("verdict")
        if "verdict_in" in when:
            allowed = [str(v) for v in (when.get("verdict_in") or [])]
            return note(verdict in allowed, {"evidence": key, "verdict": verdict, "verdict_in": allowed})
        if "verdict_not_in" in when:
            denied = [str(v) for v in (when.get("verdict_not_in") or [])]
            return note(verdict is not None and verdict not in denied, {"evidence": key, "verdict": verdict, "verdict_not_in": denied})
        if "ok" in when:
            want = bool(when["ok"])
            return note(bool(ev and bool(ev.get("ok")) == want), {"evidence": key, "want_ok": want, "verdict": verdict, "present": ev is not None})
        raise ValueError("evidence 叶子缺少判定条件（is / ok / verdict_in / verdict_not_in / ge / le）：%r" % (when,))

    unknown = [k for k in when.keys() if k not in LEAF_KEYS and k not in GROUP_KEYS]
    raise ValueError("不是合法的 when 表达式（未知键 %s）：%r" % (", ".join(unknown), when))


def compile_states(
    states: Any,
    order: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """把结构表的 states 段整理成有序列表。

    `states` 既可以是 `{name: {...}}`（按 index 排序，缺 index 按 order / 原顺序），
    也可以是 `[{name, ...}]`（已带 name 的列表）。
    """
    items: List[Dict[str, Any]] = []
    if isinstance(states, dict):
        for name, body in states.items():
            body = body or {}
            index = body.get("index")
            if index is None and order:
                index = list(order).index(name) if name in order else None
            items.append({"name": str(name), "index": index, "when": body.get("when"), "body": body})
    elif isinstance(states, (list, tuple)):
        for pos, body in enumerate(states):
            body = body or {}
            name = str(body.get("name") or body.get("state") or "")
            if not name:
                raise ValueError("states 列表第 %d 项缺少 name" % (pos,))
            index = body.get("index", pos)
            items.append({"name": name, "index": index, "when": body.get("when"), "body": body})
    else:
        raise ValueError("states 必须是对象或列表")

    for pos, item in enumerate(items):
        if item["index"] is None:
            item["index"] = pos
    items.sort(key=lambda it: (it["index"], it["name"]))
    return items


def derive_state_declarative(
    states: Any,
    evidence: Optional[Dict[str, Any]] = None,
    criteria: Optional[Dict[str, Any]] = None,
    *,
    prerequisites: Optional[Dict[str, str]] = None,
    order: Optional[Dict[str, Sequence[str]]] = None,
    scope: Optional[str] = "qubit",
    require_when: bool = False,
) -> Dict[str, Any]:
    """按声明式 `when` 推导状态：取满足的最深一个。

    返回 `{state, state_index, matched, missing_when, trace}`。
    `require_when=True` 时，除第一个状态外没有 `when` 的状态视为"永不满足"并记进
    `missing_when`（默认 False 是为了兼容 pair/tunable 还没写表达式的阶段）。
    """
    evidence = evidence or {}
    criteria = criteria or load_criteria()
    items = compile_states(states, order=(order or {}).get(scope or "") if order else None)
    if not items:
        raise ValueError("states 为空，无法推导状态")

    matched: List[Dict[str, Any]] = []
    missing_when: List[str] = []
    chosen = items[0]["name"]
    chosen_index = items[0]["index"]
    trace: List[Dict[str, Any]] = []
    for pos, item in enumerate(items):
        when = item["when"]
        if when is None and require_when and pos > 0:
            missing_when.append(item["name"])
            matched.append({"state": item["name"], "ok": False, "reason": "no_when"})
            continue
        inner: List[Dict[str, Any]] = []
        ok = eval_when(
            when,
            evidence,
            criteria,
            prerequisites=prerequisites,
            order=order,
            scope=scope,
            trace=inner,
        )
        matched.append({"state": item["name"], "ok": ok})
        if ok:
            chosen = item["name"]
            chosen_index = item["index"]
            trace = inner
    return {
        "state": chosen,
        "state_index": chosen_index,
        "matched": matched,
        "missing_when": missing_when,
        "trace": trace,
    }


def explain_when(
    when: Any,
    evidence: Optional[Dict[str, Any]] = None,
    criteria: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """给前端/自检用：返回求值结果与逐叶子的判断过程。"""
    trace: List[Dict[str, Any]] = []
    ok = eval_when(when, evidence, criteria, trace=trace, **kwargs)
    return {"ok": ok, "trace": trace}

"""Router：状态机路由判定（设计稿 §7 / BACKLOG O3 · P2）。

纯函数边界（设计稿 §7.1）：
  * **不做 IO** —— 不读文件、不查库、不发请求。结构表（边表 / 模板库）由调用方
    通过 ``machine=`` 或 ``environment["machine"]`` 传进来；实在没传时才回落到
    ``load_state_machine()``（只读 json）。单测里请显式传入 ``machine``，保证纯度。
  * **不用随机数** —— 同一份输入永远得到同一份输出。
  * **不读对话上下文** —— 函数签名里根本没有对话历史的入口（不变式①）。

判定链（设计稿 §7.2）：

    候选动作 a
      → [闸门] GATE-PRE / GATE-VER / GATE-TTL（任一 blocking → 整体拦下）
      → [剪枝] 候选 ∩ A(s)（不变式③：硬约束先剪枝，概率只在合法边内重分配）
      → [匹配] 模板库匹配（命中且稳定 → G1；命中但参数漂移 → G2；无适用模板 → G3）
      → [闸门外] G4（越权 / 连续失败 / 自环次数用尽 → hold，需人介入）

G0–G4 的口径见设计稿 §7.4：
  * G0 前置不满足 → ``reject``，并返回 ``missing``（还缺哪些证据 / 状态）。
    **不允许**靠概率或 LLM 绕过。
  * G1 模板命中且近期稳定 → ``accept``，直接用模板的动作与参数。
  * G2 模板在但参数漂移 → ``accept`` + ``requires_param_refresh: true``。
  * G3 合法边存在但没有适用模板 → ``ask_llm``；LLM 只能在 ``allowed_edges`` 内选，
    产出必须落成 qwf（不变式②）。
  * G4 越权或连续失败 → ``hold`` + ``needs_human: true``。越权只在**该动作的写语义**
    需要闸门而闸门没开时成立：只读动作（``action_kind == "read"``）的 accept 免授权，
    写参数 / 未知动作仍按 ``config/state_machine.json`` 的 ``action_semantics`` 判定。

返回（设计稿 §7.3）::

    {decision, branch, allowed_edges, chosen_action, reason, evidence_ref,
     needs_human, confidence, gates}
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from services.common.state_machine import (
    evaluate_gates,
    load_state_machine,
    resolve_edges,
)

# 模板成功率做 Bayesian 平滑时的伪计数（设计稿 §9.1：(success + α·prior) / (n + α)）
DEFAULT_ALPHA = 1.0
# 没有先验可依时的默认先验
DEFAULT_PRIOR = 0.5
# 模板默认晋升阈值 / 连续成功次数（配置缺省时用这两个）
DEFAULT_PROMOTE_THRESHOLD = 0.8
DEFAULT_SUCCESS_STREAK = 3
# 同类动作连续失败达多少次就不再让 Router 自己拍板
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3

# 动作的写语义（结构真源 = config/state_machine.json 的 action_semantics）。
# 配置缺失时用这份内置表兜底，保证 Router 被单独调用时也能判定授权。
DEFAULT_ACTION_SEMANTICS = {
    "default_kind": "unknown",
    "kinds": {
        "read": {"needs_any": []},
        "write": {"needs_any": ["allow_set_params"]},
        "execute": {"needs_any": ["allow_execute"]},
        "unknown": {"needs_any": ["allow_execute", "allow_set_params"]},
    },
    "fns": {
        "sq.iqraw": "read",
        "sq.t1": "read",
        "sq.ramsey_df": "read",
        "sq.ramsey": "read",
        "sq.xeb": "read",
        "sq.s21": "read",
        "sq.spectroscopy": "write",
        "sq.spectroscopy_iq": "write",
        "sq.pidf": "write",
        "sq.piamp": "write",
        "sq.time_rabi": "write",
        "sq.rabi": "write",
        "sq.set_pi": "write",
    },
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _action_semantics(machine: Dict[str, Any]) -> Dict[str, Any]:
    """合并 ``machine["action_semantics"]``：配置优先，缺项取内置兜底。"""
    merged = {
        "default_kind": DEFAULT_ACTION_SEMANTICS["default_kind"],
        "kinds": {k: dict(v) for k, v in DEFAULT_ACTION_SEMANTICS["kinds"].items()},
        "fns": dict(DEFAULT_ACTION_SEMANTICS["fns"]),
    }
    conf = _as_dict(_as_dict(machine).get("action_semantics"))
    if conf.get("default_kind"):
        merged["default_kind"] = str(conf["default_kind"])
    for kind, spec in _as_dict(conf.get("kinds")).items():
        base = merged["kinds"].get(kind) or {}
        spec = _as_dict(spec)
        needs = spec.get("needs_any")
        if needs is None:
            needs = base.get("needs_any") or []
        merged["kinds"][str(kind)] = {"needs_any": [str(x) for x in needs]}
    for fn, kind in _as_dict(conf.get("fns")).items():
        merged["fns"][str(fn)] = str(kind)
    return merged


def _normalize_fn(value: Any) -> str:
    """``sq.t1(q0)`` / ``sq.t1`` → ``sq.t1``（去掉调用括号与首尾空白）。"""
    text = str(value or "").strip()
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    return text


def _action_kind(semantics: Dict[str, Any], action: Any) -> str:
    """动作 → ``read|write|execute|unknown``（未登记的动作落 ``default_kind``）。"""
    action = _as_dict(action)
    fn = _normalize_fn(action.get("fn") or action.get("function"))
    kinds = _as_dict(semantics.get("kinds"))
    raw = _as_dict(semantics.get("fns")).get(fn) or semantics.get("default_kind") or "unknown"
    kind = str(raw)
    return kind if kind in kinds else "unknown"


def _authorization_for(
    semantics: Dict[str, Any], action: Any, actuation: Dict[str, Any]
) -> Dict[str, Any]:
    """动作的写授权判定：kind → ``needs_any``（其中任一闸门打开即满足）。"""
    kind = _action_kind(semantics, action)
    spec = _as_dict(_as_dict(semantics.get("kinds")).get(kind))
    needs = [str(x) for x in (spec.get("needs_any") or [])]
    flags = _as_dict(actuation)
    missing = [flag for flag in needs if not bool(flags.get(flag))]
    return {
        "kind": kind,
        "needs_any": needs,
        "missing": missing,
        "satisfied": not missing,
        "read_only": kind == "read",
    }


def _normalize_actions(actions: Any) -> List[Dict[str, Any]]:
    """候选动作归一化：允许 `{"fn": ...}` / `{"edge_id": ...}` / 纯字符串。"""
    out: List[Dict[str, Any]] = []
    for item in actions or []:
        if isinstance(item, str):
            item = {"fn": item}
        if not isinstance(item, dict):
            continue
        act = dict(item)
        fn = act.get("fn") or act.get("function")
        edge_id = act.get("edge_id") or act.get("edge")
        if not fn and not edge_id:
            continue
        act["fn"] = str(fn) if fn else None
        act["edge_id"] = str(edge_id) if edge_id else None
        act["params"] = _as_dict(act.get("params"))
        out.append(act)
    return out


def _normalize_state(state: Any) -> Dict[str, Any]:
    if isinstance(state, str):
        return {"state": state, "evidence": {}, "criteria_version": None}
    return dict(state or {})


def _template_fns(template: Dict[str, Any]) -> List[str]:
    action = _as_dict(_as_dict(template).get("action"))
    fns: List[str] = []
    if action.get("fn"):
        fns.append(str(action["fn"]))
    for alt in action.get("alternatives") or []:
        fns.append(str(alt))
    return fns


def _bayesian_success_rate(template: Dict[str, Any]) -> float:
    """模板成功率的 Bayesian 平滑（设计稿 §9.1）。"""
    tpl = _as_dict(template)
    observed = _as_dict(tpl.get("observed"))
    try:
        success = float(observed.get("success") or 0.0)
        failure = float(observed.get("failure") or 0.0)
    except (TypeError, ValueError):
        success = failure = 0.0
    prior = tpl.get("prior_success_rate")
    if prior is None:
        prior = tpl.get("success_rate")
    if prior is None:
        prior = DEFAULT_PRIOR
    try:
        prior = float(prior)
    except (TypeError, ValueError):
        prior = DEFAULT_PRIOR
    n = success + failure
    if n <= 0.0:
        return round(prior, 4)
    return round((success + DEFAULT_ALPHA * prior) / (n + DEFAULT_ALPHA), 4)


def _match_template(
    templates: Any,
    edge_id: Optional[str],
    fn: Optional[str],
) -> Optional[Dict[str, Any]]:
    """按边（优先）匹配模板；同一批里选成功率最高、id 字典序最小的那个。"""
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[int, float, str]] = None
    for tpl in templates or []:
        if not isinstance(tpl, dict):
            continue
        if str(tpl.get("status") or "").lower() == "retired":
            continue
        tpl_edge = tpl.get("edge") or tpl.get("edge_id")
        if edge_id:
            if not tpl_edge or str(tpl_edge) != str(edge_id):
                continue
        elif tpl_edge:
            continue
        tpl_fns = _template_fns(tpl)
        if fn and tpl_fns and fn not in tpl_fns:
            # fn 对不上时仍然可用（模板给的是这条边的规范写法），但排序上靠后
            fn_match = 0
        else:
            fn_match = 1 if (fn and tpl_fns) else 0
        key = (-fn_match, -_bayesian_success_rate(tpl), str(tpl.get("id") or ""))
        if best_key is None or key < best_key:
            best, best_key = tpl, key
    return best


def _is_stable(
    template: Dict[str, Any],
    fn: Optional[str],
    history: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """「近期稳定」判定（设计稿 §9.2）：成功率达标，且近期连续成功次数够。"""
    tpl = _as_dict(template)
    rate = _bayesian_success_rate(tpl)
    try:
        threshold = float(tpl.get("promote_threshold", DEFAULT_PROMOTE_THRESHOLD))
    except (TypeError, ValueError):
        threshold = DEFAULT_PROMOTE_THRESHOLD
    try:
        needed = int(tpl.get("consecutive_success_for_promote", DEFAULT_SUCCESS_STREAK))
    except (TypeError, ValueError):
        needed = DEFAULT_SUCCESS_STREAK
    streak = int(_as_dict(history.get("success_streak")).get(fn) or 0)
    status = str(tpl.get("status") or "normal")
    stable = rate >= threshold and (streak >= needed or status == "promoted")
    return stable, {
        "success_rate": rate,
        "promote_threshold": threshold,
        "success_streak": streak,
        "streak_required": needed,
        "template_status": status,
    }


def _params_drift(
    template: Dict[str, Any],
    record: Dict[str, Any],
    tolerance: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """参数漂移判定（G2）：模板记的取值基准跟当前证据对不上。

    模板可以带 ``params_evidence``（``{证据键: 当时写模板时的取值}``）；
    当前结论记录里的同一证据键与之偏差超阈值 → 参数需要重新标定。
    """
    tpl = _as_dict(template)
    baseline = _as_dict(tpl.get("params_evidence"))
    if not baseline:
        return None
    evidence = _as_dict(_as_dict(record).get("evidence"))
    for key in sorted(baseline):
        expected = baseline[key]
        current = evidence.get(key)
        if current is None:
            continue
        cur_val = current.get("value") if isinstance(current, dict) else current
        if (
            isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and isinstance(cur_val, (int, float))
            and not isinstance(cur_val, bool)
        ):
            rel = abs(float(cur_val) - float(expected)) / max(abs(float(expected)), 1e-9)
            if rel > tolerance:
                return {
                    "key": key,
                    "expected": expected,
                    "current": cur_val,
                    "relative_diff": round(rel, 4),
                }
        elif str(expected) != str(cur_val):
            return {
                "key": key,
                "expected": expected,
                "current": cur_val,
                "relative_diff": None,
            }
    return None


def _evidence_refs(record: Dict[str, Any], history: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    evidence = _as_dict(_as_dict(record).get("evidence"))
    for key in sorted(evidence):
        item = evidence[key]
        run_id = item.get("run_id") if isinstance(item, dict) else None
        if run_id and str(run_id) not in refs:
            refs.append(str(run_id))
    for ref in history.get("evidence_ref") or []:
        if ref and str(ref) not in refs:
            refs.append(str(ref))
    return refs


def _gate_name(gate: Any) -> str:
    """闸门名统一成 ``GATE-XXX``（state_machine 内部用 PRE/VER/TTL 简写）。"""
    name = str(gate or "").strip()
    if not name:
        return "GATE-UNKNOWN"
    return name if name.upper().startswith("GATE-") else "GATE-" + name.upper()


def _aggregate_gates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把每个候选动作的闸门结果压成「每个闸门一条」。"""
    agg: Dict[str, bool] = {}
    for row in rows:
        name = str(row.get("gate") or "")
        passed = bool(row.get("passed"))
        if name not in agg:
            agg[name] = passed
        else:
            agg[name] = agg[name] and passed
    return [{"gate": name, "passed": agg[name]} for name in sorted(agg)]


def route(
    state: Any,
    actions: Any,
    criteria: Any = None,
    history: Any = None,
    environment: Any = None,
    machine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """状态机路由判定（设计稿 §7）。

    入参：
      * `state` —— 状态结论记录（``build_state_record()`` 的产物，或纯状态名）。
      * `actions` —— 候选动作列表（``{"fn": ...}`` / ``{"edge_id": ...}``）。
      * `criteria` —— 判据上下文：``{"version": "1.0.0", "confidence": 0.9}``。
      * `history` —— 历史摘要：``{"attempts": {fn: n}, "consecutive_failures": {fn: n},
        "success_streak": {fn: n}, "evidence_ref": [...] }``。
      * `environment` —— 环境：``{"actuation": {"allow_execute": bool,
        "allow_set_params": bool}, "now": ts, "machine": {...}}``。
      * `machine` —— 结构表（边表 + 模板库）；不传则回落 ``load_state_machine()``。

    返回：见模块头部；``decision`` ∈ ``accept|hold|ask_llm|reject``，
    ``branch`` ∈ ``G0|G1|G2|G3|G4``。
    """
    record = _normalize_state(state)
    candidates = _normalize_actions(actions)
    crit = _as_dict(criteria)
    hist = _as_dict(history)
    env = _as_dict(environment)

    machine = machine or env.get("machine") or load_state_machine()
    machine = _as_dict(machine)
    now = env.get("now")
    if now is None:
        now = time.time()
    criteria_version = crit.get("version") or env.get("criteria_version")
    current_state = str(record.get("state") or "unknown")
    templates = machine.get("templates") or []

    actuation = _as_dict(env.get("actuation"))

    try:
        max_failures = int(env.get("max_consecutive_failures") or DEFAULT_MAX_CONSECUTIVE_FAILURES)
    except (TypeError, ValueError):
        max_failures = DEFAULT_MAX_CONSECUTIVE_FAILURES
    try:
        drift_tolerance = float(env.get("drift_tolerance") or 0.0)
    except (TypeError, ValueError):
        drift_tolerance = 0.0

    attempts = _as_dict(hist.get("attempts"))
    failures = _as_dict(hist.get("consecutive_failures"))
    streaks = _as_dict(hist.get("success_streak"))

    result: Dict[str, Any] = {
        "decision": "reject",
        "branch": "G0",
        "current_state": current_state,
        "allowed_edges": [],
        "chosen_action": None,
        "reason": "",
        "evidence_ref": _evidence_refs(record, hist),
        "needs_human": False,
        "confidence": 0.0,
        "gates": [],
        "missing": None,
        "requires_param_refresh": False,
        "requires_authorization": False,
        "template": None,
        "candidates": [],
        "llm_task": None,
        "action_kind": None,
        "authorization": None,
    }

    # ---- 闸门：PRE 逐候选，VER / TTL 是记录级的整体闸门 ------------------
    gate_rows: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    survivors: List[Dict[str, Any]] = []
    pre_failures: List[Dict[str, Any]] = []
    needs_rerun = False

    if not candidates:
        result["gates"] = []
        result["reason"] = "没有候选动作，无法判定路由"
        result["missing"] = {"states": [], "evidence": [], "edges": []}
        return result

    for cand in candidates:
        gates = evaluate_gates(
            record,
            action=cand,
            criteria_version=criteria_version,
            now=now,
            machine=machine,
        )
        for key in ("PRE", "VER", "TTL"):
            info = _as_dict((gates.get("gates") or {}).get(key))
            gate_rows.append(
                {
                    "gate": info.get("id") or ("GATE-" + key),
                    "passed": bool(info.get("ok")),
                }
            )
        pre_ok = True
        for violation in gates.get("violations") or []:
            gate_name = str(violation.get("gate") or "")
            if gate_name == "PRE":
                if violation.get("blocking"):
                    pre_ok = False
                    pre_failures.append(
                        {
                            "action": cand,
                            "violation": violation.get("violation"),
                            "detail": _as_dict(violation.get("detail")),
                        }
                    )
            elif violation.get("blocking"):
                blocked.append(violation)
            else:
                needs_rerun = True
        if pre_ok:
            survivors.append(
                {
                    "action": cand,
                    "resolution": resolve_edges(cand, current_state, machine),
                }
            )

    result["gates"] = _aggregate_gates(gate_rows)
    if needs_rerun:
        result["needs_rerun"] = True

    # VER / TTL 是记录级闸门：一旦 blocking，整体拦下（设计稿 §7.2）
    if blocked:
        blocked_by = sorted({_gate_name(v.get("gate")) for v in blocked})
        recovery = ""
        for violation in blocked:
            detail = _as_dict(violation.get("detail"))
            if detail.get("recovery"):
                recovery = str(detail["recovery"])
        result["decision"] = "hold"
        result["branch"] = "G4"
        result["needs_human"] = True
        result["blocked_by"] = blocked_by
        result["violations"] = blocked
        result["reason"] = "被 %s 闸门拦下%s" % (
            "、".join(blocked_by),
            ("：" + recovery) if recovery else "",
        )
        return result

    # ---- 剪枝：候选 ∩ A(s)（不变式③：先剪枝，概率只在合法边内重分配）----
    if not survivors:
        missing = {"states": [], "evidence": [], "edges": []}
        for failure in pre_failures:
            detail = failure["detail"]
            for state_name in detail.get("required_state") or []:
                if state_name not in missing["states"]:
                    missing["states"].append(str(state_name))
            for key in detail.get("missing_evidence") or []:
                if key not in missing["evidence"]:
                    missing["evidence"].append(str(key))
            for edge_id in detail.get("candidate_edges") or []:
                if edge_id not in missing["edges"]:
                    missing["edges"].append(str(edge_id))
        result["decision"] = "reject"
        result["branch"] = "G0"
        result["missing"] = missing
        result["reason"] = "候选动作都不在当前态的合法边集合 A(%s) 内" % current_state
        return result

    allowed_edges: List[str] = []
    for item in survivors:
        for edge_id in item["resolution"]["legal_ids"]:
            if edge_id not in allowed_edges:
                allowed_edges.append(str(edge_id))
    result["allowed_edges"] = allowed_edges

    # ---- G4 前置筛选：自环次数用尽 / 同类动作连续失败 --------------------
    viable: List[Dict[str, Any]] = []
    exhausted: List[Dict[str, Any]] = []
    for item in survivors:
        cand = item["action"]
        fn = cand.get("fn")
        legal_edges = item["resolution"]["legal"]
        edge_id = legal_edges[0].get("id") if legal_edges else None
        blocked_edge = None
        for edge in legal_edges:
            if str(edge.get("kind") or "") != "self":
                continue
            try:
                limit = int(edge.get("max_attempts") or 0)
            except (TypeError, ValueError):
                limit = 0
            if limit <= 0:
                continue
            used = int(attempts.get(fn) or attempts.get(edge.get("id")) or 0)
            if used >= limit:
                blocked_edge = {
                    "edge": edge.get("id"),
                    "fn": fn,
                    "max_attempts": limit,
                    "attempts": used,
                    "reason": "自环次数用尽（%s 已用 %d/%d 次）" % (edge.get("id"), used, limit),
                }
                break
        if blocked_edge is not None:
            exhausted.append(blocked_edge)
            continue
        try:
            fails = int(failures.get(fn) or 0)
        except (TypeError, ValueError):
            fails = 0
        if max_failures > 0 and fails >= max_failures:
            exhausted.append(
                {
                    "edge": edge_id,
                    "fn": fn,
                    "attempts": fails,
                    "reason": "同类动作连续失败达上限（%s 连续失败 %d 次）" % (fn, fails),
                }
            )
            continue
        viable.append(item)

    if not viable:
        result["decision"] = "hold"
        result["branch"] = "G4"
        result["needs_human"] = True
        result["exhausted"] = exhausted
        result["reason"] = "；".join(sorted({str(e.get("reason")) for e in exhausted})) or "没有可用候选"
        return result

    # ---- 模板匹配：G1 / G2 / G3 ------------------------------------------
    matched: List[Dict[str, Any]] = []
    for item in viable:
        cand = item["action"]
        fn = cand.get("fn")
        legal_edges = item["resolution"]["legal"]
        edge_id = legal_edges[0].get("id") if legal_edges else None
        template = _match_template(templates, edge_id, fn)
        stable = False
        stable_info: Dict[str, Any] = {}
        drift: Optional[Dict[str, Any]] = None
        if template:
            stable, stable_info = _is_stable(template, fn, hist)
            drift = _params_drift(template, record, drift_tolerance)
        matched.append(
            {
                "action": cand,
                "edge_id": edge_id,
                "template": template,
                "stable": stable,
                "stable_info": stable_info,
                "drift": drift,
            }
        )

    result["candidates"] = [
        {
            "fn": m["action"].get("fn"),
            "edge_id": m["edge_id"],
            "intent": m["action"].get("intent"),
            "template_id": (m["template"] or {}).get("id"),
            "stable": bool(m["stable"]),
        }
        for m in matched
    ]

    def _chosen_from_template(entry: Dict[str, Any]) -> Dict[str, Any]:
        template = _as_dict(entry["template"])
        tpl_action = _as_dict(template.get("action"))
        params = _as_dict(tpl_action.get("params"))
        qubit = record.get("qubit")
        if qubit:
            params = {
                key: (qubit if isinstance(value, str) and value == "$qubit" else value)
                for key, value in params.items()
            }
        chosen: Dict[str, Any] = {
            "fn": tpl_action.get("fn") or entry["action"].get("fn"),
            "params": params,
            "edge_id": entry["edge_id"],
            "template_id": template.get("id"),
        }
        if entry["action"].get("intent"):
            chosen["intent"] = entry["action"]["intent"]
        return chosen

    g1 = [m for m in matched if m["template"] and m["stable"] and not m["drift"]]
    g2 = [m for m in matched if m["template"] and m["drift"] and not m["stable"]]
    g2_drift_only = [m for m in matched if m["template"] and m["drift"]]

    if g1:
        entry = g1[0]
        result["decision"] = "accept"
        result["branch"] = "G1"
        result["chosen_action"] = _chosen_from_template(entry)
        result["template"] = entry["template"].get("id")
        result["confidence"] = entry["stable_info"].get("success_rate", 0.0)
        result["reason"] = "模板 %s 命中，近期稳定（成功率 %s ≥ 阈值 %s）" % (
            entry["template"].get("id"),
            entry["stable_info"].get("success_rate"),
            entry["stable_info"].get("promote_threshold"),
        )
    elif g2 or g2_drift_only:
        entry = (g2 or g2_drift_only)[0]
        result["decision"] = "accept"
        result["branch"] = "G2"
        result["chosen_action"] = _chosen_from_template(entry)
        result["template"] = entry["template"].get("id")
        result["requires_param_refresh"] = True
        result["confidence"] = entry["stable_info"].get("success_rate", 0.0)
        result["param_drift"] = entry["drift"]
        result["reason"] = "模板 %s 命中，但参数漂移（%s），需重新标定后再执行" % (
            entry["template"].get("id"),
            (entry["drift"] or {}).get("key"),
        )
    else:
        # G3：合法边存在但没有适用模板 → 交给 LLM，但只能在合法边内选
        result["decision"] = "ask_llm"
        result["branch"] = "G3"
        confidence = crit.get("confidence")
        result["confidence"] = float(confidence) if confidence is not None else 0.5
        result["llm_task"] = {
            "allowed_edges": allowed_edges,
            "allowed_actions": [
                {
                    "fn": m["action"].get("fn"),
                    "edge_id": m["edge_id"],
                    "intent": m["action"].get("intent"),
                }
                for m in matched
            ],
            "instruction": "只在 allowed_edges 内选一个动作，产出必须落成 qwf",
        }
        result["reason"] = "合法边存在但模板库里没有适用写法，交 LLM 在合法边内选（产出须落成 qwf）"

    # ---- G4：越权（写授权） --------------------------------------------------
    # 授权口径：accept 要不要授权，看**这条动作**需要哪个闸门（action_semantics）——
    #   * read（只测不写）→ 不需要授权，accept 直接成立；
    #   * write / execute / unknown → 按登记的 needs_any 判定（任一打开即满足）。
    # G3 只是产出候选 qwf 供人看，真要下发时执行层还会再挡一道闸门，故这里不拦。
    if result["decision"] == "accept":
        semantics = _action_semantics(machine)
        auth = _authorization_for(semantics, result["chosen_action"], actuation)
        result["action_kind"] = auth["kind"]
        result["authorization"] = auth
        if not auth["satisfied"]:
            result["decision"] = "hold"
            result["branch"] = "G4"
            result["needs_human"] = True
            result["requires_authorization"] = True
            result["reason"] = "该动作是 %s，需要 %s；当前 %s 未打开，需人工授权后重判" % (
                auth["kind"],
                " 或 ".join(auth["needs_any"]) or "写授权",
                " / ".join(auth["missing"]),
            )

    return result

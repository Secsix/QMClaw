"""状态机的**结构层**：合法边 A(s)、三道闸门参数、状态结论记录。

与 `services.common.qubit_state` 的分工（红线：不改 `qubit_state.py` 的判定口径，
它同时被 `vendor/openqwf/qwf/qubit_state.py` 镜像，见设计稿 §12.3）：

* `qubit_state.py` 负责「证据 → 状态」的**纯判定**（单向推进，取满足的最深态）；
* 本模块负责**结构**：合法边 A(s)、每态 TTL、闸门配置、以及带凭据/版本/时效的
  「状态结论记录」（设计稿 §5.2）。

结构真源 = `config/state_machine.json`；阈值真源仍是 `config/lab_criteria.json`
（边表是结构、阈值是数值，混在一起改阈值会污染结构版本 —— 设计稿 D2）。

纯函数、无 IO 副作用（除了读 json），可离线单测。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_ROOT = os.path.dirname(os.path.dirname(_HERE))  # .../server
DEFAULT_STATE_MACHINE_PATH = os.path.join(SERVER_ROOT, "config", "state_machine.json")

_CACHE: Dict[str, Dict[str, Any]] = {}


def load_state_machine(path: Optional[str] = None) -> Dict[str, Any]:
    """读取 state_machine.json（带进程内缓存，path 不同则分开缓存）。"""
    target = os.path.abspath(path or DEFAULT_STATE_MACHINE_PATH)
    cached = _CACHE.get(target)
    if cached is None:
        with open(target, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        _CACHE[target] = cached
    return cached


def state_version(machine: Optional[Dict[str, Any]] = None) -> str:
    """结构版本（写进状态结论记录，用于解释"结论是按哪版结构下的"）。"""
    machine = machine or load_state_machine()
    return str(machine.get("version") or "0.0.0")


def state_definitions(machine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    machine = machine or load_state_machine()
    return dict(machine.get("states") or {})


def ttl_for(state: str, machine: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """该状态的 TTL（秒）。None = 永不过期（如 unknown）。"""
    machine = machine or load_state_machine()
    states = machine.get("states") or {}
    entry = states.get(state)
    if not isinstance(entry, dict):
        return None
    ttl = entry.get("ttl_s")
    if ttl is None:
        return None
    try:
        return float(ttl)
    except (TypeError, ValueError):
        return None


def all_edges(machine: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """全部合法边（结构表；不按状态过滤）。"""
    machine = machine or load_state_machine()
    return [dict(edge) for edge in (machine.get("edges") or []) if isinstance(edge, dict)]


def edges_for(state: str, machine: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """A(s)：当前态 s 下允许的全部转移边（前向 + 自环 + 回退）。

    PPT 原话：「A(s) 就是三类边的总称……任何模板、任何 agent 生成的动作，
    只能从这三类边里选，不能自创第四类。」
    """
    machine = machine or load_state_machine()
    return [edge for edge in all_edges(machine) if edge.get("from") == state]


def edge_by_id(edge_id: str, machine: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    for edge in all_edges(machine):
        if edge.get("id") == edge_id:
            return edge
    return None


def gate_config(machine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """三道闸门（GATE-PRE / GATE-VER / GATE-TTL）的参数。"""
    machine = machine or load_state_machine()
    gates = machine.get("gates") or {}
    return {key: dict(value) for key, value in gates.items() if isinstance(value, dict)}


def templates(machine: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """模板库（P3 落地；此前为空）。"""
    machine = machine or load_state_machine()
    return [dict(t) for t in (machine.get("templates") or []) if isinstance(t, dict)]


def _evidence_provenance(
    key: str,
    value: Any,
    source: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """把 `state_evidence` 的 sources 条目整形成结论记录里的凭据。"""
    source = source or {}
    fns = source.get("functions") or []
    measured_at = source.get("finished_at")
    if not isinstance(measured_at, (int, float)):
        measured_at = None
    origin = source.get("source")
    if not origin:
        origin = "sq.%s" % fns[0] if fns else "unknown"
    return {
        "key": key,
        "value": value,
        "run_id": source.get("run_id"),
        "dataset_ids": list(source.get("dataset_ids") or []),
        "measured_at": measured_at,
        "source": origin,
        "functions": list(fns),
        "status": source.get("status"),
        "dataset_source": source.get("dataset_source"),
    }


#: 公开别名：pair scope 也要把 evidence 整形成"能复核的凭据"，逻辑完全一样。
evidence_provenance = _evidence_provenance


def build_state_record(
    qubit: str,
    state_result: Dict[str, Any],
    evidence: Optional[Dict[str, Any]] = None,
    sources: Optional[Dict[str, Any]] = None,
    conflicts: Optional[List[Dict[str, Any]]] = None,
    criteria_version: Optional[str] = None,
    now: Optional[float] = None,
    machine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造**状态结论记录**（设计稿 §5.2）。

    结论 = 状态 + 凭据 + 判据版本 + 时效。没有凭据就无法复核，没有版本就无法解释
    判据变化，没有时效就无法发现过期。

    入参 `state_result` 是 `qubit_state.derive_state()` 的返回值（只读消费，不改它）。
    """
    machine = machine or load_state_machine()
    state_result = state_result or {}
    state = str(state_result.get("state") or "unknown")
    decided_at = float(now if now is not None else time.time())
    ttl = ttl_for(state, machine)

    evidence_map: Dict[str, Any] = {}
    for key, value in (evidence or {}).items():
        evidence_map[key] = _evidence_provenance(key, value, (sources or {}).get(key))

    return {
        "qubit": str(qubit),
        "scope": {
            "type": "qubit",
            "qubits": [str(qubit)],
            "coupler": None,
        },
        "state": state,
        "state_index": state_result.get("state_index"),
        "state_version": state_version(machine),
        "criteria_version": criteria_version or state_result.get("criteria_version"),
        "evidence": evidence_map,
        "conflicts": list(conflicts or []),
        "reasons": list(state_result.get("reasons") or []),
        "missing": list(state_result.get("missing") or []),
        "decided_at": decided_at,
        "expires_at": (decided_at + ttl) if ttl is not None else None,
    }


def record_validity(
    record: Dict[str, Any],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """状态结论是否仍然有效（GATE-TTL 的判定基础；**纯计算**）。

    返回 ``{"valid", "expired", "expires_at", "reason"}``。
    """
    record = record or {}
    current = float(now if now is not None else time.time())
    expires_at = record.get("expires_at")
    if expires_at is None:
        return {
            "valid": True,
            "expired": False,
            "expires_at": None,
            "reason": "该状态无 TTL（永不过期）",
        }
    try:
        deadline = float(expires_at)
    except (TypeError, ValueError):
        return {
            "valid": True,
            "expired": False,
            "expires_at": expires_at,
            "reason": "expires_at 不可解析，按不过期处理",
        }
    expired = current > deadline
    return {
        "valid": not expired,
        "expired": expired,
        "expires_at": deadline,
        "reason": "状态已过期，需重判" if expired else "状态在有效期内",
    }


# ---------------------------------------------------------------------------
# scope 视图（单比特 / 双比特 pair / 后续可扩展）
# ---------------------------------------------------------------------------
#
# 设计稿 §8.1：CZ 状态机与单比特状态机是**两套并列的 scope**，共享同一套判据引擎与
# 同一套证据契约，但状态集与边表独立。因此这里的做法不是把 Router / 闸门写两遍，
# 而是把「结构表」按 scope 取一个视图喂给同一批纯函数。
#
# v1.3.0 起 scope 由结构表的 ``scopes`` 段**声明**（states_key / edges_key /
# enabled_by / engine / prerequisite），没有该段时回落到 qubit + pair 的旧键，
# 行为与 v1.2.0 完全一致。新增 scope 只需要写配置，不再需要新的 Python 模块。
#
# 这样 ttl_for / all_edges / edges_for / resolve_edges / evaluate_gates / route
# 一行都不用改，只是吃不同的表。

SCOPE_QUBIT = "qubit"
SCOPE_PAIR = "pair"
SCOPES = (SCOPE_QUBIT, SCOPE_PAIR)

#: 结构表没有 ``scopes`` 段时的兜底声明（与 v1.2.0 的两套表一一对应）。
_SCOPE_FALLBACK_SPECS: Dict[str, Dict[str, Any]] = {
    SCOPE_QUBIT: {
        "states_key": "states",
        "edges_key": "edges",
        "enabled_by": [],
        "engine": "legacy",
        "label": "单比特六态",
    },
    SCOPE_PAIR: {
        "states_key": "pair_states",
        "edges_key": "pair_edges",
        "enabled_by": [],
        "engine": "legacy",
        "label": "双比特 CZ",
    },
}


def scope_specs(machine: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """scope 声明表：优先读结构表的 ``scopes`` 段，缺省从旧键推导。

    每个 scope 可声明：``states_key`` / ``edges_key`` / ``enabled_by``（芯片能力标签）/
    ``engine``（legacy = qubit_state.py，config = state_expr.py）/ ``label`` /
    ``prerequisite``（前置 scope 与状态）。qubit 永远排在最前。
    """
    machine = machine or load_state_machine()
    declared = machine.get("scopes")
    specs: Dict[str, Dict[str, Any]] = {}
    if isinstance(declared, dict) and declared:
        for raw_name, raw_spec in declared.items():
            if not isinstance(raw_spec, dict):
                continue
            name = str(raw_name).strip().lower()
            fallback = dict(_SCOPE_FALLBACK_SPECS.get(name) or {})
            merged: Dict[str, Any] = {**fallback, **dict(raw_spec)}
            merged.setdefault(
                "states_key", "states" if name == SCOPE_QUBIT else "%s_states" % name
            )
            merged.setdefault(
                "edges_key", "edges" if name == SCOPE_QUBIT else "%s_edges" % name
            )
            merged.setdefault("enabled_by", [])
            merged.setdefault("engine", "legacy")
            specs[name] = merged
    if not specs:
        specs = {name: dict(spec) for name, spec in _SCOPE_FALLBACK_SPECS.items()}
    ordered: Dict[str, Dict[str, Any]] = {}
    if SCOPE_QUBIT in specs:
        ordered[SCOPE_QUBIT] = specs[SCOPE_QUBIT]
    for name, spec in specs.items():
        if name != SCOPE_QUBIT:
            ordered[name] = spec
    return ordered


def scope_names(machine: Optional[Dict[str, Any]] = None) -> List[str]:
    """结构表声明了哪些 scope（顺序即展示顺序）。"""
    return list(scope_specs(machine).keys())


def machine_for_scope(
    scope: str,
    machine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """取出某个 scope 的结构视图（``states`` / ``edges`` 换成该 scope 自己的）。"""
    machine = machine or load_state_machine()
    name = str(scope or SCOPE_QUBIT).strip().lower()
    specs = scope_specs(machine)
    if name not in specs:
        raise ValueError("未知 scope %r；可用：%s" % (scope, ", ".join(specs)))
    spec = specs[name]
    states_key = str(spec.get("states_key") or "states")
    edges_key = str(spec.get("edges_key") or "edges")
    states = machine.get(states_key)
    edges = machine.get(edges_key)
    # 边表允许是空列表（能力档案把某个 scope 的边全禁掉时就是这样），
    # 但键必须存在 —— 缺键才是「没有这个 scope」。
    if not isinstance(states, dict) or not states or not isinstance(edges, list):
        raise ValueError(
            "结构表里没有 %s scope（缺 %s / %s）" % (name, states_key, edges_key)
        )
    view = dict(machine)
    view["states"] = {k: dict(v) for k, v in states.items() if isinstance(v, dict)}
    view["edges"] = [dict(e) for e in edges if isinstance(e, dict)]
    view["scope"] = name
    # 新 scope（pulse / tunable）的证据契约写在 scope 声明里而不是顶层，
    # 必须并入视图，否则 _config_state_table 会返回空的 evidence_sources。
    for key in ("evidence_sources", "missing_by_state", "prerequisite"):
        if key in spec:
            view[key] = spec[key]
    return view


def pair_machine(machine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """pair scope 的结构视图（等价于 ``machine_for_scope("pair")``）。"""
    return machine_for_scope(SCOPE_PAIR, machine)


def has_scope(scope: str, machine: Optional[Dict[str, Any]] = None) -> bool:
    """结构表里是否有这个 scope 的表。"""
    machine = machine or load_state_machine()
    name = str(scope or "").strip().lower()
    specs = scope_specs(machine)
    spec = specs.get(name)
    if not spec:
        return False
    states = machine.get(str(spec.get("states_key") or ""))
    edges = machine.get(str(spec.get("edges_key") or ""))
    return isinstance(states, dict) and bool(states) and isinstance(edges, list)


def scope_states(
    scope: str = SCOPE_QUBIT,
    machine: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """按 index 排出某个 scope 的状态序列。"""
    view = machine_for_scope(scope, machine)
    states = view.get("states") or {}
    rows = [
        (int(info.get("index", 0)), name)
        for name, info in states.items()
        if isinstance(info, dict)
    ]
    return [name for _, name in sorted(rows)]


def _config_state_table(scope: str, view: Dict[str, Any]) -> Dict[str, Any]:
    """配置驱动 scope 的状态表（形状与 ``qubit_state.state_table()`` 对齐，便于前端复用）。"""
    states = view.get("states") or {}
    rows = sorted(
        (
            (int(info.get("index", 0)), name, info)
            for name, info in states.items()
            if isinstance(info, dict)
        ),
        key=lambda row: row[0],
    )
    table: Dict[str, Any] = {
        "scope": scope,
        "states": [name for _, name, _ in rows],
        "descriptions": {name: str(info.get("description") or "") for _, name, info in rows},
        "next_action": {name: str(info.get("next_action") or "") for _, name, info in rows},
        "evidence_sources": {
            key: dict(value)
            for key, value in (view.get("evidence_sources") or {}).items()
            if isinstance(value, dict)
        },
        "missing_by_state": {
            key: list(value)
            for key, value in (view.get("missing_by_state") or {}).items()
            if isinstance(value, (list, tuple))
        },
    }
    return table


def state_table_extended(
    machine: Optional[Dict[str, Any]] = None,
    scope: str = SCOPE_QUBIT,
) -> Dict[str, Any]:
    """状态机全表 = 该 scope 的状态表 + 边表 + 闸门 + 结构版本。

    旧表字段**一字不改**地保留在结果里，前端与自检脚本按新表读即可。
    """
    name = str(scope or SCOPE_QUBIT).strip().lower()
    machine = machine or load_state_machine()
    specs = scope_specs(machine)
    if name not in specs:
        raise ValueError("未知 scope %r；可用：%s" % (scope, ", ".join(specs)))
    view = machine_for_scope(name, machine)
    gates = gate_config(machine)
    version = state_version(machine)
    ttl = {key: ttl_for(key, view) for key in (view.get("states") or {})}

    if name == SCOPE_PAIR:
        from services.common.pair_state import state_table_pair

        table = dict(state_table_pair())
    elif name == SCOPE_QUBIT and str(specs[name].get("engine") or "legacy") == "legacy":
        from services.common.qubit_state import state_table

        table = dict(state_table())
    else:
        table = _config_state_table(name, view)

    table["scope"] = name
    table["edges"] = all_edges(view)
    table["gates"] = gates
    table["state_version"] = version
    table["state_ttl"] = ttl
    return table


# ---------------------------------------------------------------------------
# 三道闸门求值（P1 / BACKLOG O2）
# ---------------------------------------------------------------------------
#
# 三道闸门（设计稿 §6）：
#   * GATE-PRE  前置状态没到位 —— 动作所在边的 from 态 ≠ 当前态；
#   * GATE-VER  判据版本对不上 —— 结论/动作锁定的判据版本 ≠ 当前判据版本；
#   * GATE-TTL  状态已过期 —— now > expires_at。
#
# 纯计算、只读：不连真机、不写台账、不问 LLM。除了读结构表 json 之外没有任何 IO，
# 所以可以离线单测，也可以放在 Router 前面当「动作下发前的最后一道关」。


def _action_fns(action: Optional[Dict[str, Any]]) -> List[str]:
    fn = (action or {}).get("fn") or (action or {}).get("function")
    return [str(fn)] if fn else []


def action_matches_edge(action: Optional[Dict[str, Any]], edge: Dict[str, Any]) -> bool:
    """动作是否命中这条边（fn 相同，或 fn 落在 alternatives 里）。"""
    if not action or not edge:
        return False
    edge_action = edge.get("action") or {}
    candidates = {edge_action.get("fn")}
    candidates.update(edge_action.get("alternatives") or [])
    candidates.discard(None)
    return bool(candidates & set(_action_fns(action)))


def resolve_edges(
    action: Optional[Dict[str, Any]],
    state: Optional[str] = None,
    machine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把动作解析成候选边（跨态），并区分「在 A(s) 内」与「别的态才有」。

    支持两种动作写法：
      * `{"edge_id": "unknown->freq_locked"}` —— 直接指定结构边；
      * `{"fn": "sq.spectroscopy"}` —— 按函数名匹配（含 alternatives）。
    """
    machine = machine or load_state_machine()
    edges = all_edges(machine)
    edge_id = (action or {}).get("edge_id") or (action or {}).get("edge")
    if edge_id:
        candidates = [e for e in edges if e.get("id") == edge_id]
    else:
        candidates = [e for e in edges if action_matches_edge(action, e)]
    legal = [e for e in candidates if state is None or e.get("from") == state]
    legal_ids = [e.get("id") for e in legal]
    foreign = [e for e in candidates if e.get("id") not in legal_ids]
    return {
        "candidates": candidates,
        "legal": legal,
        "legal_ids": legal_ids,
        "foreign": foreign,
    }


def _evidence_keys(record: Dict[str, Any]) -> List[str]:
    return list(((record or {}).get("evidence") or {}).keys())


def _required_evidence_for_state(
    state: str,
    machine: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """某个状态要「算数」所需要的证据键（states[state].requires）。"""
    machine = machine or load_state_machine()
    entry = (machine.get("states") or {}).get(state) or {}
    return list(entry.get("requires") or [])


def evaluate_gates(
    record: Dict[str, Any],
    action: Optional[Dict[str, Any]] = None,
    criteria_version: Optional[str] = None,
    now: Optional[float] = None,
    machine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """三道闸门求值：这个动作**能不能下**。

    入参：
      * `record` —— 状态结论记录（`build_state_record()` 的产物）；
      * `action` —— 待下发的动作（`{"fn": ...}` 或 `{"edge_id": ...}`）；
      * `criteria_version` —— 当前判据版本（`config/lab_criteria.json` 的 version）；
        传 None 时退回用记录里的版本，无法判定就跳过版本核对；
      * `now` —— 当前时间戳（默认取 `time.time()`）。

    返回：`{"ok", "decision", "current_state", "allowed_edges", "candidate_edges",
    "gates", "violations", "blocked_by", "needs_rerun", "reason"}`。

    `ok=False` 表示有闸门拦下了动作（`blocked_by` 指出是哪几道）。
    """
    machine = machine or load_state_machine()
    record = record or {}
    action = action or {}
    gates_cfg = gate_config(machine)
    current_state = str(record.get("state") or "unknown")
    have = set(_evidence_keys(record))

    # ---- GATE-PRE：前置状态没到位 -------------------------------------
    resolution = resolve_edges(action, current_state, machine)
    legal = resolution["legal"]
    candidates = resolution["candidates"]
    pre_violation: Optional[str] = None
    pre_detail: Dict[str, Any] = {"current_state": current_state}
    if not candidates:
        pre_violation = "unknown_action"
        pre_detail = {
            "current_state": current_state,
            "reason": "该动作不在结构边表内 —— 不能自创第四类边",
            "action": dict(action),
            "note": "结构层面的隐法边\uff1bRouter 的 G0 会以 branch=g0 报同一件事",
        }
    elif not legal:
        required_states = sorted({str(e.get("from")) for e in candidates})
        missing: List[str] = []
        for name in required_states:
            for key in _required_evidence_for_state(name, machine):
                if key not in missing and key not in have:
                    missing.append(key)
        pre_violation = "precondition_not_met"
        pre_detail = {
            "current_state": current_state,
            "required_state": required_states,
            "missing_evidence": missing,
            "candidate_edges": [e.get("id") for e in candidates],
            "reason": "动作只在别的态下合法，先把状态推进到 %s" % "、".join(required_states),
        }
    else:
        pending: List[str] = []
        for edge in legal:
            for key in edge.get("requires") or []:
                if key not in pending and key not in have:
                    pending.append(key)
        pre_detail = {
            "current_state": current_state,
            "allowed_edges": resolution["legal_ids"],
            "pending_evidence": pending,
            "reason": "动作在当前态的合法边集合 A(s) 内",
        }

    # ---- GATE-VER：判据版本对不上 -------------------------------------
    ver_cfg = gates_cfg.get("VER") or {}
    policy = str(ver_cfg.get("on_version_mismatch") or "block")
    current_version = criteria_version if criteria_version is not None else record.get("criteria_version")
    action_version = action.get("criteria_version")
    if not action_version:
        for edge in legal:
            action_version = ((edge.get("guard") or {}).get("criteria_version"))
            if action_version:
                break
    ver_violation: Optional[str] = None
    ver_detail: Dict[str, Any] = {
        "policy": policy,
        "current_criteria_version": current_version,
        "record_criteria_version": record.get("criteria_version"),
        "action_criteria_version": action_version,
    }
    if current_version is None:
        ver_detail["reason"] = "没有当前判据版本可比对，跳过版本核对"
    else:
        record_version = record.get("criteria_version")
        if record_version and record_version != current_version:
            ver_violation = "record_criteria_version_mismatch"
            ver_detail["record_mismatch"] = {
                "record": record_version,
                "current": current_version,
            }
        if action_version and action_version != current_version:
            if ver_violation is None:
                ver_violation = "action_criteria_version_mismatch"
            ver_detail["action_mismatch"] = {
                "action": action_version,
                "current": current_version,
            }
        if ver_violation is None:
            ver_detail["reason"] = "判据版本一致"

    # ---- GATE-TTL：状态已过期 -----------------------------------------
    ttl_cfg = gates_cfg.get("TTL") or {}
    validity = record_validity(record, now)
    ttl_violation = "state_expired" if validity.get("expired") else None
    ttl_detail = {
        "expires_at": validity.get("expires_at"),
        "valid": validity.get("valid"),
        "reason": validity.get("reason"),
        "recovery": ttl_cfg.get("recovery"),
    }

    # ---- 汇总 -----------------------------------------------------------
    violations: List[Dict[str, Any]] = []
    for gate_id, violation, detail in (
        ("PRE", pre_violation, pre_detail),
        ("VER", ver_violation, ver_detail),
        ("TTL", ttl_violation, ttl_detail),
    ):
        if violation is None:
            continue
        blocking = True
        if gate_id == "VER" and policy == "rerun":
            # 配置成 rerun：命中版本不一致后按当前判据重判一次再放行
            blocking = False
        violations.append({
            "gate": gate_id,
            "violation": violation,
            "blocking": blocking,
            "detail": detail,
        })
    blocked_by = [v["gate"] for v in violations if v["blocking"]]
    ok = not blocked_by
    if ok and violations:
        reason = "闸门提示需要重判：%s" % "、".join(v["violation"] for v in violations)
    elif ok:
        reason = "三道闸门均通过"
    else:
        reason = "被 %s 闸门拦下" % "、".join(blocked_by)
    return {
        "ok": ok,
        "decision": "allow" if ok else "block",
        "current_state": current_state,
        "allowed_edges": resolution["legal_ids"],
        "candidate_edges": [e.get("id") for e in candidates],
        "gates": {
            "PRE": {
                "id": "GATE-PRE",
                "ok": pre_violation is None,
                "violation": pre_violation,
                "detail": pre_detail,
            },
            "VER": {
                "id": "GATE-VER",
                "ok": ver_violation is None,
                "violation": ver_violation,
                "detail": ver_detail,
            },
            "TTL": {
                "id": "GATE-TTL",
                "ok": ttl_violation is None,
                "violation": ttl_violation,
                "detail": ttl_detail,
            },
        },
        "violations": violations,
        "blocked_by": blocked_by,
        "needs_rerun": bool(violations) and policy == "rerun",
        "reason": reason,
    }

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
SERVER_ROOT = os.path.dirname(os.path.dirname(_HERE))  # .../qmclaw-server
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


def state_table_extended(machine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """状态机全表 = 旧表（qubit_state.state_table）+ 边表 + 闸门 + 结构版本。

    旧表字段**一字不改**地保留在结果里，前端与自检脚本按新表读即可。
    """
    from services.common.qubit_state import state_table

    machine = machine or load_state_machine()
    table = dict(state_table())
    table["edges"] = all_edges(machine)
    table["gates"] = gate_config(machine)
    table["state_version"] = state_version(machine)
    table["state_ttl"] = {
        name: ttl_for(name, machine) for name in (machine.get("states") or {})
    }
    return table

"""芯片能力档案：把「状态图长什么样」变成配置合并的结果。

三层叠加（本模块只做前两层，runtime 由调用方把证据/记录传进来）：::

    base    server/config/state_machine.json        所有 scope 的状态 / 边 / 标签
      ↓
    chip    server/config/device_capabilities.json  能力标签 + 显式启停 + 边的覆盖
      ↓
    runtime 演练证据 / 运行台账 / 当前状态            判定时参与，不落配置

设计约束：

* **纯读**：本模块不连真机、不写台账、不判定状态（判定仍是 qubit_state / state_expr）。
* **单一入口**：其它模块想按芯片能力裁边，只能经过 :func:`profile_machine` /
  :func:`resolve_machine`，不要自己 ``json.load`` 结构表。
* **缺文件即全启用**：没有 ``device_capabilities.json`` 时行为与「只有结构表」完全一致，
  这样老机器 / 老部署不会因为新增能力档案而改变状态图。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from services.common import state_machine as sm

_HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_ROOT = os.path.dirname(os.path.dirname(_HERE))  # .../server
DEFAULT_CAPABILITIES_PATH = os.path.join(SERVER_ROOT, "config", "device_capabilities.json")

#: 能力档案的缓存：path -> (mtime, data)。改文件即失效，不需要重启服务。
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

MISSING_NOTE = "没有 device_capabilities.json：全部 scope 按结构表启用"


def load_capabilities(
    path: Optional[str] = None,
    *,
    refresh: bool = False,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """读芯片能力档案，返回 ``(data, path)``；文件不存在时返回 ``({}, None)``。"""
    target = os.path.abspath(path or DEFAULT_CAPABILITIES_PATH)
    try:
        mtime = os.path.getmtime(target)
    except OSError:
        return {}, None
    cached = _CACHE.get(target)
    if cached is not None and not refresh and cached[0] == mtime:
        return dict(cached[1]), target
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}, None
    if not isinstance(data, dict):
        return {}, None
    _CACHE[target] = (mtime, data)
    return dict(data), target


def capability_tags(caps: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """能力标签（``tunable`` / ``coupler`` / ``drag_required`` …）。"""
    if caps is None:
        caps, _ = load_capabilities()
    tags = caps.get("capabilities") if isinstance(caps, dict) else None
    return dict(tags) if isinstance(tags, dict) else {}


def _scope_override(caps: Optional[Dict[str, Any]], scope: str) -> Dict[str, Any]:
    if not isinstance(caps, dict):
        return {}
    scopes = caps.get("scopes")
    if not isinstance(scopes, dict):
        return {}
    entry = scopes.get(scope)
    return dict(entry) if isinstance(entry, dict) else {}


def scope_status(
    scope: str,
    machine: Optional[Dict[str, Any]] = None,
    caps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """这个 scope 在当前芯片下是否启用，以及**为什么**。"""
    machine = machine or sm.load_state_machine()
    name = str(scope or sm.SCOPE_QUBIT).strip().lower()
    specs = sm.scope_specs(machine)
    if name not in specs:
        return {
            "scope": name,
            "enabled": False,
            "reason": "结构表里没有这个 scope（可用：%s）" % ", ".join(specs),
            "enabled_by": [],
            "missing": [],
            "source": "structure",
        }
    spec = specs[name]
    enabled_by = [str(tag) for tag in (spec.get("enabled_by") or [])]
    tags = capability_tags(caps)
    # 没有任何能力档案时不做能力过滤（老部署行为不变）：档案存在才按标签裁。
    missing = [tag for tag in enabled_by if not bool(tags.get(tag))] if caps else []
    override = _scope_override(caps, name)
    states_table = machine.get(spec.get("states_key") or "")
    edges_table = machine.get(spec.get("edges_key") or "")
    has_tables = isinstance(states_table, dict) and bool(states_table) and isinstance(
        edges_table, list
    )
    status: Dict[str, Any] = {
        "scope": name,
        "label": str(spec.get("label") or name),
        "engine": str(spec.get("engine") or "legacy"),
        "enabled_by": enabled_by,
        "missing": missing,
        "prerequisite": spec.get("prerequisite"),
    }
    if not has_tables:
        status.update(
            enabled=False,
            source="structure",
            reason="结构表缺 %s / %s" % (spec.get("states_key"), spec.get("edges_key")),
        )
        return status
    if "enabled" in override:
        status.update(
            enabled=bool(override.get("enabled")),
            source="override",
            reason="人工覆盖：device_capabilities.scopes.%s.enabled=%s"
            % (name, bool(override.get("enabled"))),
        )
        return status
    if missing:
        status.update(
            enabled=False,
            source="capabilities",
            reason="能力未满足：缺 %s" % "、".join(missing),
        )
        return status
    if not caps:
        status.update(enabled=True, source="structure", reason="没有能力档案：按结构表启用")
        return status
    status.update(
        enabled=True,
        source="capabilities" if enabled_by else "structure",
        reason="能力满足：%s" % "、".join(enabled_by) if enabled_by else "无能力要求，按结构表启用",
    )
    return status


def edge_overrides(caps: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    if caps is None:
        caps, _ = load_capabilities()
    raw = caps.get("edge_overrides") if isinstance(caps, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


_PATCH_FIELDS = ("max_attempts", "requires", "guard", "note", "action", "kind", "enabled")


def apply_edge_overrides(
    edges: List[Dict[str, Any]],
    overrides: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """按边的 id 打补丁（``enabled: false`` 直接裁掉），返回 ``(edges, 被裁掉的 id)``。"""
    if not overrides:
        return [dict(e) for e in edges], []
    kept: List[Dict[str, Any]] = []
    removed: List[str] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        edge_id = str(edge.get("id") or "")
        patch = overrides.get(edge_id)
        item = dict(edge)
        if patch:
            if patch.get("enabled") is False:
                removed.append(edge_id)
                continue
            for field in _PATCH_FIELDS:
                if field in patch and field != "enabled":
                    item[field] = patch[field]
        kept.append(item)
    return kept, removed


def profile_machine(
    machine: Optional[Dict[str, Any]] = None,
    caps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """基底结构 + 芯片覆盖 → 这台机器上「生效的」结构表（带启用标记，不改原表）。"""
    base = machine or sm.load_state_machine()
    if caps is None:
        caps, _ = load_capabilities()
    overrides = edge_overrides(caps)
    view: Dict[str, Any] = dict(base)
    specs = sm.scope_specs(base)
    annotated: Dict[str, Dict[str, Any]] = {}
    for name, spec in specs.items():
        status = scope_status(name, base, caps)
        annotated[name] = {**dict(spec), "enabled": status["enabled"], "reason": status["reason"]}
        states_key = spec.get("states_key") or ""
        edges_key = spec.get("edges_key") or ""
        if edges_key and base.get(edges_key):
            kept, removed = apply_edge_overrides(base.get(edges_key) or [], overrides)
            view[edges_key] = kept
            if removed:
                annotated[name]["disabled_edges"] = removed
        if states_key and base.get(states_key):
            view[states_key] = {
                k: dict(v) for k, v in (base.get(states_key) or {}).items() if isinstance(v, dict)
            }
    view["scopes"] = annotated
    view["capabilities"] = capability_tags(caps)
    return view


def enabled_scopes(
    machine: Optional[Dict[str, Any]] = None,
    caps: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """该芯片启用的 scope 清单（顺序与结构表声明一致）。"""
    base = machine or sm.load_state_machine()
    if caps is None:
        caps, _ = load_capabilities()
    rows: List[Dict[str, Any]] = []
    for name, spec in sm.scope_specs(base).items():
        status = scope_status(name, base, caps)
        view = sm.machine_for_scope(name, base) if status["enabled"] else None
        kept, removed = apply_edge_overrides(
            (view or {}).get("edges") or [], edge_overrides(caps)
        )
        rows.append(
            {
                "scope": name,
                "label": status.get("label"),
                "enabled": status["enabled"],
                "reason": status["reason"],
                "source": status["source"],
                "enabled_by": status["enabled_by"],
                "missing": status["missing"],
                "engine": status["engine"],
                "prerequisite": status.get("prerequisite"),
                "states": sm.scope_states(name, base) if status["enabled"] else [],
                "edge_count": len(kept),
                "disabled_edges": removed,
            }
        )
    return rows


def resolve_machine(
    scope: str = sm.SCOPE_QUBIT,
    machine: Optional[Dict[str, Any]] = None,
    caps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按芯片能力解析某个 scope 的结构视图（禁用的 scope 直接报错，不静默降级）。"""
    base = machine or sm.load_state_machine()
    if caps is None:
        caps, _ = load_capabilities()
    name = str(scope or sm.SCOPE_QUBIT).strip().lower()
    status = scope_status(name, base, caps)
    if not status["enabled"]:
        raise ValueError("scope %r 在当前芯片能力下未启用：%s" % (name, status["reason"]))
    prof = profile_machine(base, caps)
    return sm.machine_for_scope(name, prof)


def explain(
    scope: str = sm.SCOPE_QUBIT,
    machine: Optional[Dict[str, Any]] = None,
    caps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """回答「这张图为什么长这样」：scope 的启用依据 + 每条边的去留。"""
    base = machine or sm.load_state_machine()
    if caps is None:
        caps, _ = load_capabilities()
    status = scope_status(scope, base, caps)
    result: Dict[str, Any] = dict(status)
    if not status["enabled"]:
        result["edges"] = []
        return result
    prof = profile_machine(base, caps)
    view = sm.machine_for_scope(scope, prof)
    overrides = edge_overrides(caps)
    result["edges"] = [
        {
            "id": edge.get("id"),
            "kind": edge.get("kind"),
            "from": edge.get("from"),
            "to": edge.get("to"),
            "fn": ((edge.get("action") or {}).get("fn")),
            "overridden": str(edge.get("id")) in overrides,
        }
        for edge in (view.get("edges") or [])
    ]
    result["disabled_edges"] = sorted(overrides) if overrides else []
    return result


def summarize(
    machine: Optional[Dict[str, Any]] = None,
    caps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """``GET /qubit/scopes`` 的返回体：这台机器的能力、启用了哪些 scope、结构版本。"""
    base = machine or sm.load_state_machine()
    if caps is None:
        caps, _ = load_capabilities()
    loaded_path = DEFAULT_CAPABILITIES_PATH if caps else None
    if caps:
        try:
            os.path.getmtime(loaded_path or "")
        except (OSError, TypeError):
            loaded_path = None
    return {
        "success": True,
        "structure_version": sm.state_version(base),
        "chip": (caps or {}).get("chip"),
        "capabilities": capability_tags(caps),
        "capabilities_source": "config/device_capabilities.json" if caps else MISSING_NOTE,
        "scopes": enabled_scopes(base, caps),
    }

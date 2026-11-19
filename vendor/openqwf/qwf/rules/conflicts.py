"""规则冲突检测（F1 验收的核心：加一条规则要能被检出与既有规则的冲突）。

四类冲突，按严重度分 error / warning：

| code | 级别 | 含义 | 检出条件 |
|---|---|---|---|
| `C_CONTRADICTORY` | error | 两条同优先级规则可能同时命中，动作却不同 | trigger 可同时满足 ∧ priority 相等 ∧ action 指纹不同 |
| `C_DUP_ID` | error | id 重复 | 加载期已拦（loader），这里兜底 |
| `C_SHADOWED` | warning | 高优先级规则完全覆盖了低优先级规则的触发域 | 条件集合是超集 ∧ action 指纹不同 |
| `C_UNREACHABLE` | error | trigger 自相矛盾，永远不会命中 | 同字段的等值/区间/枚举互相排斥 |
| `C_LOW_CONF_HIGH_PRIO` | warning | priority ≥ 80 但 confidence < 0.5 | 单条即可判定 |
| `C_NO_PROVENANCE` | warning | 没有 provenance（学到的规则不知从哪来） | 单条即可判定 |

"trigger 可同时满足"用一个**保守的区间/枚举交叠判定**来做，不做 SAT 求解：

- 每个字段收集约束：等值集合、数值区间（lo, hi，闭开按 op 化）、枚举集合、否定集合；
- 同一字段：等值 ∩ 枚举 ≠ ∅ 且 区间 ∩ ≠ ∅ 且 不撞否定集 ⇒ 该字段可满足；
- 所有字段都可满足 ⇒ 两个 trigger 可同时命中（`all` 部分）；
- 任一方有 `any` 子句时退化为「认为可同时命中」（宁可多报，规则包小、人工复核便宜）；
- `none` 子句只作为"排除"，不参与可满足性判定（保守：忽略它）。

这个近似会**高估**冲突（假阳性），但不会漏掉真冲突——对"回归套件能检出冲突"的验收
要求来说，宁可多报。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .loader import RuleError

ERROR_CODES = {"C_CONTRADICTORY", "C_UNREACHABLE", "C_DUP_ID"}

#: 同一字段的数值区间表示：(lo, hi, lo_open, hi_open)
Interval = Tuple[Optional[float], Optional[float], bool, bool]


# ── 动作指纹 ────────────────────────────────────────────────────────────────
def action_fingerprint(action: Dict[str, Any]) -> str:
    """动作的语义指纹：同指纹 = 同一个动作（不是冲突）。"""
    kind = action.get("type")
    if kind == "set":
        payload = {"type": "set", "path": action.get("path"),
                   "value": action.get("value"), "delta": action.get("delta"),
                   "deltaPercent": action.get("deltaPercent"),
                   "params": action.get("params") or {}}
    elif kind == "retry":
        payload = {"type": "retry", "experiment": action.get("experiment"),
                   "params": action.get("params") or {},
                   "maxAttempts": action.get("maxAttempts", 1)}
    elif kind == "halt":
        # halt 的理由不同不算不同动作（都是"停"）
        payload = {"type": "halt"}
    elif kind == "branch":
        payload = {"type": "branch", "choice": action.get("choice")}
    else:  # note
        payload = {"type": "note", "text": action.get("text")}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _action_target(action: Dict[str, Any]) -> str:
    """冲突粒度：同一个"目标"上的互斥动作才算冲突。"""
    kind = action.get("type")
    if kind == "set":
        return f"set:{action.get('path')}"
    if kind == "retry":
        return f"retry:{action.get('experiment')}"
    if kind == "branch":
        return "branch"
    if kind == "halt":
        return "halt"
    return f"note:{action.get('text')}"


# ── 区间 / 枚举 ────────────────────────────────────────────────────────────
def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _interval_of(cond: Dict[str, Any]) -> Optional[Interval]:
    op = cond["op"]
    num = _as_number(cond.get("value"))
    if num is None:
        return None
    if op == "==":
        return (num, num, False, False)
    if op == "<":
        return (None, num, False, True)
    if op == "<=":
        return (None, num, False, False)
    if op == ">":
        return (num, None, True, False)
    if op == ">=":
        return (num, None, False, False)
    return None


def _intervals_overlap(a: Interval, b: Interval) -> bool:
    (alo, ahi, alo_open, ahi_open) = a
    (blo, bhi, blo_open, bhi_open) = b
    # 下界：取较紧的那个
    if alo is None:
        lo, lo_open = blo, blo_open
    elif blo is None:
        lo, lo_open = alo, alo_open
    elif alo > blo:
        lo, lo_open = alo, alo_open
    elif blo > alo:
        lo, lo_open = blo, blo_open
    else:
        lo, lo_open = alo, (alo_open or blo_open)
    # 上界
    if ahi is None:
        hi, hi_open = bhi, bhi_open
    elif bhi is None:
        hi, hi_open = ahi, ahi_open
    elif ahi < bhi:
        hi, hi_open = ahi, ahi_open
    elif bhi < ahi:
        hi, hi_open = bhi, bhi_open
    else:
        hi, hi_open = ahi, (ahi_open or bhi_open)

    if lo is None or hi is None:
        return True
    if lo > hi:
        return False
    if lo == hi and (lo_open or hi_open):
        return False
    return True


class _FieldDomain:
    """单条规则内、单个字段上的约束汇总。"""

    __slots__ = ("equals", "intervals", "enums", "negations", "patterns", "exists")

    def __init__(self) -> None:
        self.equals: List[Any] = []
        self.intervals: List[Interval] = []
        self.enums: List[List[Any]] = []
        self.negations: List[Any] = []
        self.patterns: List[str] = []
        self.exists: Optional[bool] = None

    def satisfiable(self) -> bool:
        # 等值 / 枚举 / 区间 三者求交
        candidates: Optional[List[Any]] = None
        if self.equals:
            candidates = list(self.equals)
        if self.enums:
            narrowed: List[Any] = []
            for values in self.enums:
                if candidates is None:
                    narrowed = list(values)
                else:
                    narrowed = [v for v in candidates if v in values]
                candidates = narrowed
        if candidates is not None:
            for value in candidates:
                num = _as_number(value)
                if self.intervals and num is not None:
                    if not all(_intervals_overlap((num, num, False, False), iv) for iv in self.intervals):
                        continue
                if any(value == neg for neg in self.negations):
                    continue
                return True
            return False
        # 只有区间约束：两两可交
        for i in range(len(self.intervals)):
            for j in range(i + 1, len(self.intervals)):
                if not _intervals_overlap(self.intervals[i], self.intervals[j]):
                    return False
        return True


def _domains(trigger: Dict[str, Any]) -> Dict[str, _FieldDomain]:
    out: Dict[str, _FieldDomain] = {}
    for cond in trigger.get("all") or []:
        dom = out.setdefault(cond["field"], _FieldDomain())
        op, value = cond["op"], cond.get("value")
        if op == "==":
            dom.equals.append(value)
        elif op in ("<", "<=", ">", ">="):
            iv = _interval_of(cond)
            if iv is not None:
                dom.intervals.append(iv)
        elif op == "in":
            dom.enums.append(list(value))
        elif op == "not_in":
            dom.negations.extend(list(value))
        elif op == "!=":
            dom.negations.append(value)
        elif op == "matches":
            dom.patterns.append(str(value))
        elif op == "exists":
            dom.exists = True
    return out


def trigger_satisfiable(rule: Dict[str, Any]) -> Tuple[bool, str]:
    """trigger 自身是否可能命中；返回 (可满足, 原因)。"""
    trigger = rule["trigger"]
    for dom_field, dom in _domains(trigger).items():
        if not dom.satisfiable():
            return False, f"字段 {dom_field} 的约束互斥（等值/枚举/区间/否定无交集）"
    return True, ""


def _overlap_unknown(trigger: Dict[str, Any]) -> bool:
    """含 `any` / `always` 的 trigger 不做精确判定（保守认为可同时命中）。"""
    return bool(trigger.get("any")) or bool(trigger.get("always"))


def triggers_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """两条 trigger 是否可能同时命中（保守近似）。"""
    ta, tb = a["trigger"], b["trigger"]
    if _overlap_unknown(ta) or _overlap_unknown(tb):
        return True
    da, db = _domains(ta), _domains(tb)
    for field in set(da) & set(db):
        merged = _FieldDomain()
        for dom in (da[field], db[field]):
            merged.equals.extend(dom.equals)
            merged.intervals.extend(dom.intervals)
            merged.enums.extend(dom.enums)
            merged.negations.extend(dom.negations)
            merged.patterns.extend(dom.patterns)
        if not merged.satisfiable():
            return False
    return True


def _interval_contains(outer: Interval, inner: Interval) -> bool:
    """outer 区间是否完整包含 inner。"""
    olo, ohi, olo_open, ohi_open = outer
    ilo, ihi, ilo_open, ihi_open = inner
    if olo is not None:
        if ilo is None:
            return False
        if olo > ilo:
            return False
        if olo == ilo and olo_open and not ilo_open:
            return False
    if ohi is not None:
        if ihi is None:
            return False
        if ohi < ihi:
            return False
        if ohi == ihi and ohi_open and not ihi_open:
            return False
    return True


def _domain_contains(hi: "_FieldDomain", lo: "_FieldDomain") -> bool:
    """hi 的约束是否**不紧于** lo（hi 覆盖 lo 的全部取值）。"""
    if hi.equals:
        if lo.equals:
            if not set(lo.equals) <= set(hi.equals):
                return False
        elif lo.enums:
            flat = [v for values in lo.enums for v in values]
            if not all(v in hi.equals for v in flat):
                return False
        else:
            return False
    if hi.intervals:
        if not lo.intervals:
            return False
        if not all(any(_interval_contains(h, l) for h in hi.intervals) for l in lo.intervals):
            return False
    if hi.enums:
        flat_hi = [v for values in hi.enums for v in values]
        if lo.equals:
            if not all(v in flat_hi for v in lo.equals):
                return False
        elif lo.enums:
            flat_lo = [v for values in lo.enums for v in values]
            if not all(v in flat_hi for v in flat_lo):
                return False
        else:
            return False
    if not set(hi.negations) <= set(lo.negations):
        return False
    if set(hi.patterns) != set(lo.patterns):
        return False
    if hi.exists and not lo.exists:
        return False
    return True


def _subsumes(high: Dict[str, Any], low: Dict[str, Any]) -> bool:
    """high 的触发域是否 ⊇ low 的触发域（即 high 完全压制 low）。

    判定：high 的约束字段是 low 的子集（约束更少 ⇒ 覆盖更宽），且每个共有
    字段上 high 的约束不紧于 low；high 若带 `none` 而 low 没有，则不算覆盖。
    """
    th, tl = high["trigger"], low["trigger"]
    if _overlap_unknown(th) or _overlap_unknown(tl):
        return False
    if th.get("none") and not tl.get("none"):
        return False
    dh, dl = _domains(th), _domains(tl)
    if not set(dh).issubset(set(dl)):
        return False
    return all(_domain_contains(dom_h, dl[field]) for field, dom_h in dh.items())


# ── 主入口 ──────────────────────────────────────────────────────────────────
def find_conflicts(rules: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """扫描规则包，返回冲突列表（每条含 code/level/ids/message）。"""
    conflicts: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}

    for idx, rule in enumerate(rules):
        rid = rule.get("id")
        if rid in seen:
            conflicts.append({
                "code": "C_DUP_ID", "level": "error", "ids": [rid],
                "message": f"规则 id 重复：{rid!r}（#{seen[rid]} 与 #{idx}）",
            })
        else:
            seen[rid] = idx

        ok, reason = trigger_satisfiable(rule)
        if not ok:
            conflicts.append({
                "code": "C_UNREACHABLE", "level": "error", "ids": [rid],
                "message": f"{rid} 的 trigger 永远不命中：{reason}",
            })

        if int(rule.get("priority", 0)) >= 80 and float(rule.get("confidence", 1.0)) < 0.5:
            conflicts.append({
                "code": "C_LOW_CONF_HIGH_PRIO", "level": "warning", "ids": [rid],
                "message": (f"{rid} 的 priority={rule.get('priority')} 但 confidence="
                            f"{rule.get('confidence')}；高优先级低把握，容易压过可靠规则"),
            })

        if not rule.get("provenance"):
            conflicts.append({
                "code": "C_NO_PROVENANCE", "level": "warning", "ids": [rid],
                "message": f"{rid} 没有 provenance；学到的规则应能追溯来源",
            })

    for i in range(len(rules)):
        for j in range(i + 1, len(rules)):
            a, b = rules[i], rules[j]
            if a.get("id") == b.get("id"):
                continue
            if action_fingerprint(a["action"]) == action_fingerprint(b["action"]):
                continue
            if _action_target(a["action"]) != _action_target(b["action"]):
                # 目标不同（如一个设 f10、一个设 PiGate.amp）⇒ 不冲突，可以都生效
                continue
            if not triggers_overlap(a, b):
                continue
            pa, pb = int(a.get("priority", 0)), int(b.get("priority", 0))
            ca, cb = float(a.get("confidence", 0.0)), float(b.get("confidence", 0.0))
            if pa == pb and ca == cb:
                conflicts.append({
                    "code": "C_CONTRADICTORY", "level": "error", "ids": [a["id"], b["id"]],
                    "message": (f"{a['id']} 与 {b['id']} 可能同时命中且优先级/置信度相同，"
                                f"但动作不同（{_action_target(a['action'])}）；"
                                "调开 priority 或合并成一条"),
                })
            elif pa > pb and _subsumes(a, b):
                conflicts.append({
                    "code": "C_SHADOWED", "level": "warning", "ids": [a["id"], b["id"]],
                    "message": (f"{b['id']} 的触发域被更高优先级的 {a['id']} 完全覆盖，"
                                "低优先级规则永远不会生效"),
                })
            elif pb > pa and _subsumes(b, a):
                conflicts.append({
                    "code": "C_SHADOWED", "level": "warning", "ids": [b["id"], a["id"]],
                    "message": (f"{a['id']} 的触发域被更高优先级的 {b['id']} 完全覆盖，"
                                "低优先级规则永远不会生效"),
                })
    return conflicts


def errors_of(conflicts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [c for c in conflicts if c.get("level") == "error"]


def format_conflicts(conflicts: Sequence[Dict[str, Any]]) -> str:
    if not conflicts:
        return "规则包无冲突"
    lines = [f"规则包冲突 {len(conflicts)} 条："]
    for c in conflicts:
        mark = "✗" if c.get("level") == "error" else "⚠"
        lines.append(f"  {mark} [{c['code']}] {c['message']}")
    return "\n".join(lines)


def raise_if_errors(conflicts: Sequence[Dict[str, Any]]) -> None:
    for c in errors_of(conflicts):
        raise RuleError(c["code"], c["message"], ",".join(c.get("ids") or ()))


__all__ = [
    "ERROR_CODES",
    "action_fingerprint",
    "triggers_overlap",
    "trigger_satisfiable",
    "find_conflicts",
    "errors_of",
    "format_conflicts",
    "raise_if_errors",
]

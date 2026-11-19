"""规则引擎（F1）：把规则包在"事实"上求值，给出决策与解释。

定位是 QMClaw 三层决策里的 **L2「学到的规则」**：

    L1 RuleEngine  精确匹配，<1μs —— 专家写的硬规则（不在本模块）
    L2 本模块      模式匹配，<1ms —— 从会话/论文里沉淀、可回归验证的规则包
    L3 LLM         兜底推理，~300ms —— 无规则命中时才走

对外只有三件事：

    engine = RuleEngine(rules)                  # 加载期就检测冲突
    decision = engine.evaluate(facts)           # 命中 → Decision；无命中 → None
    engine.explain(facts)                       # 为什么选这条 / 为什么没命中

`facts` 是扁平字典，键允许点路径（`{"metrics.t1_us": 12.5}`）；求值时先按整键找，
再按点路径逐层取，最后按"末段名"唯一匹配（`{"t1_us": ...}` 也能被
`metrics.t1_us` 读到 —— 只要末段在 facts 里唯一）。这样宿主既可以直接塞扁平
metrics，也能塞嵌套 dict。

确定性要求（回归套件会断言）：同一 (rules, facts) 永远给出同一 decision，
排序键固定为 `(-priority, -confidence, id)`。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .conflicts import (
    action_fingerprint,
    errors_of,
    find_conflicts,
    format_conflicts,
    triggers_overlap,
)
from .loader import RuleError, validate_rule

_MISSING = object()


class RuleConflict(RuleError):
    """strict 模式下，加载/求值时遇到冲突。"""

    def __init__(self, conflicts: Sequence[Dict[str, Any]]):
        self.conflicts = list(conflicts)
        message = format_conflicts(self.conflicts)
        super().__init__("E_RULE_CONFLICT", message,
                         ",".join(c for cf in self.conflicts for c in (cf.get("ids") or ())))


@dataclass
class RuleMatch:
    rule_id: str
    priority: int
    confidence: float
    action: Dict[str, Any]
    bindings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    """一次决策的结果。`alternatives` 是被压过的其它候选（按同一排序键）。"""

    rule_id: str
    action: Dict[str, Any]
    priority: int
    confidence: float
    bindings: Dict[str, Any] = field(default_factory=dict)
    alternatives: List[str] = field(default_factory=list)
    conflicted: bool = False
    layer: str = "L2"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "ruleId": self.rule_id,
            "action": self.action,
            "priority": self.priority,
            "confidence": self.confidence,
            "bindings": self.bindings,
            "alternatives": self.alternatives,
            "conflicted": self.conflicted,
        }


# ── facts 取值 ──────────────────────────────────────────────────────────────
def resolve_fact(facts: Dict[str, Any], path: str) -> Any:
    """按整键 → 点路径 → 末段唯一匹配 的顺序取事实；取不到返回 `_MISSING`。"""
    if path in facts:
        return facts[path]
    node: Any = facts
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            node = _MISSING
            break
    if node is not _MISSING:
        return node
    tail = path.rsplit(".", 1)[-1]
    hits = [v for k, v in facts.items() if k.rsplit(".", 1)[-1] == tail]
    if len(hits) == 1:
        return hits[0]
    return _MISSING


def check_condition(cond: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[bool, Any]:
    """单条件求值：返回 (是否成立, 实际值)。类型不匹配一律判不成立（不抛）。"""
    value = resolve_fact(facts, cond["field"])
    op = cond["op"]
    if op == "exists":
        return value is not _MISSING and value is not None, value
    if value is _MISSING:
        return False, _MISSING
    expected = cond.get("value")
    try:
        if op == "==":
            return value == expected, value
        if op == "!=":
            return value != expected, value
        if op == "<":
            return value < expected, value
        if op == "<=":
            return value <= expected, value
        if op == ">":
            return value > expected, value
        if op == ">=":
            return value >= expected, value
        if op == "in":
            return value in expected, value
        if op == "not_in":
            return value not in expected, value
        if op == "contains":
            return expected in value, value
        if op == "matches":
            return re.search(str(expected), str(value)) is not None, value
    except TypeError:
        return False, value
    return False, value


def rule_matches(rule: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """规则是否命中；同时返回命中的条件（bindings 用，便于解释）。"""
    trigger = rule["trigger"]
    bindings: Dict[str, Any] = {}

    scope = rule.get("scope") or {}
    qubits = scope.get("qubits")
    if qubits:
        qubit = resolve_fact(facts, "qubit")
        if qubit is _MISSING or qubit not in qubits:
            return False, {}
    experiments = scope.get("experiments")
    if experiments:
        experiment = resolve_fact(facts, "experiment")
        if experiment is _MISSING or experiment not in experiments:
            return False, {}

    for cond in trigger.get("all") or []:
        ok, value = check_condition(cond, facts)
        if not ok:
            return False, {}
        bindings[cond["field"]] = None if value is _MISSING else value

    any_conds = trigger.get("any") or []
    if any_conds:
        hit = False
        for cond in any_conds:
            ok, value = check_condition(cond, facts)
            if ok:
                hit = True
                bindings[cond["field"]] = None if value is _MISSING else value
                break
        if not hit:
            return False, {}

    for cond in trigger.get("none") or []:
        ok, _ = check_condition(cond, facts)
        if ok:
            return False, {}

    return True, bindings


def _sort_key(rule: Dict[str, Any]) -> Tuple[int, float, str]:
    return (-int(rule.get("priority", 0)), -float(rule.get("confidence", 0.0)), str(rule.get("id")))


class RuleEngine:
    """规则包求值器。`strict=True` 时任何 error 级冲突都在构造期抛出。"""

    def __init__(self, rules: Sequence[Dict[str, Any]], *, strict: bool = False,
                 min_confidence: float = 0.0, source: str = ""):
        self.rules: List[Dict[str, Any]] = [validate_rule(r, source or "rules") for r in rules]
        self.strict = strict
        self.min_confidence = float(min_confidence)
        self.source = source
        self.conflicts = find_conflicts(self.rules)
        self._by_id = {r["id"]: r for r in self.rules}
        if strict:
            errors = errors_of(self.conflicts)
            if errors:
                raise RuleConflict(errors)

    # ── 查询 ────────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self.rules)

    def ids(self) -> List[str]:
        return [r["id"] for r in self.rules]

    def get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        return self._by_id.get(rule_id)

    def errors(self) -> List[Dict[str, Any]]:
        return errors_of(self.conflicts)

    # ── 求值 ────────────────────────────────────────────────────────────
    def candidates(self, facts: Dict[str, Any]) -> List[RuleMatch]:
        """全部命中规则，按 (-priority, -confidence, id) 排序。"""
        out: List[RuleMatch] = []
        for rule in self.rules:
            if float(rule.get("confidence", 0.0)) < self.min_confidence:
                continue
            ok, bindings = rule_matches(rule, facts)
            if ok:
                out.append(RuleMatch(
                    rule_id=rule["id"],
                    priority=int(rule["priority"]),
                    confidence=float(rule["confidence"]),
                    action=dict(rule["action"]),
                    bindings=bindings,
                ))
        out.sort(key=lambda m: (-m.priority, -m.confidence, m.rule_id))
        return out

    def evaluate(self, facts: Dict[str, Any]) -> Optional[Decision]:
        """命中则返回 Decision，否则 None（由调用方决定是否降级到 L3）。"""
        matches = self.candidates(facts)
        if not matches:
            return None
        top = matches[0]
        conflicted = self._has_runtime_conflict(top, matches[1:])
        if conflicted and self.strict:
            raise RuleConflict([{
                "code": "C_CONTRADICTORY", "level": "error", "ids": [top.rule_id,
                                                                     matches[1].rule_id],
                "message": (f"事实同时命中 {top.rule_id} 与 {matches[1].rule_id}，"
                            "两者动作不同且无法用优先级/置信度分辨"),
            }])
        return Decision(
            rule_id=top.rule_id,
            action=top.action,
            priority=top.priority,
            confidence=top.confidence,
            bindings=top.bindings,
            alternatives=[m.rule_id for m in matches[1:]],
            conflicted=conflicted,
        )

    def evaluate_action(self, facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """只取动作（宿主里最常见的用法）。"""
        decision = self.evaluate(facts)
        return None if decision is None else decision.action

    def _has_runtime_conflict(self, top: RuleMatch, rest: Sequence[RuleMatch]) -> bool:
        """次优候选是否在同一目标上给出不同动作且打平（同优先级同置信度）。"""
        for other in rest:
            if action_fingerprint(top.action) == action_fingerprint(other.action):
                continue
            same_target = _same_target(top.action, other.action)
            if same_target and top.priority == other.priority and top.confidence == other.confidence:
                return True
        return False

    # ── 解释 ────────────────────────────────────────────────────────────
    def explain(self, facts: Dict[str, Any], *, limit: int = 5) -> Dict[str, Any]:
        """为什么选这条 / 为什么没命中 —— 给人看的诊断，也给 LLM 当上下文。"""
        matches = self.candidates(facts)
        missed: List[Dict[str, Any]] = []
        for rule in self.rules:
            if any(m.rule_id == rule["id"] for m in matches):
                continue
            missed.append({"ruleId": rule["id"], "why": self._why_missed(rule, facts)})
        decision = self.evaluate(facts)
        return {
            "source": self.source,
            "ruleCount": len(self.rules),
            "facts": facts,
            "matched": [
                {"ruleId": m.rule_id, "priority": m.priority,
                 "confidence": m.confidence, "bindings": m.bindings}
                for m in matches[:limit]
            ],
            "missed": missed[:limit],
            "decision": None if decision is None else decision.to_dict(),
            "conflicts": self.conflicts,
        }

    def _why_missed(self, rule: Dict[str, Any], facts: Dict[str, Any]) -> str:
        if float(rule.get("confidence", 0.0)) < self.min_confidence:
            return f"confidence {rule['confidence']} < min_confidence {self.min_confidence}"
        scope = rule.get("scope") or {}
        if scope.get("qubits"):
            qubit = resolve_fact(facts, "qubit")
            if qubit is _MISSING or qubit not in scope["qubits"]:
                return f"scope.qubits 不含 {qubit!r}"
        if scope.get("experiments"):
            experiment = resolve_fact(facts, "experiment")
            if experiment is _MISSING or experiment not in scope["experiments"]:
                return f"scope.experiments 不含 {experiment!r}"
        for cond in rule["trigger"].get("all") or []:
            ok, value = check_condition(cond, facts)
            if not ok:
                shown = "<缺失>" if value is _MISSING else value
                return f"all 条件不成立：{cond['field']} {cond['op']} {cond.get('value')!r}（实际 {shown!r}）"
        any_conds = rule["trigger"].get("any") or []
        if any_conds and not any(check_condition(c, facts)[0] for c in any_conds):
            return "any 条件全不成立"
        for cond in rule["trigger"].get("none") or []:
            ok, _ = check_condition(cond, facts)
            if ok:
                return f"none 条件成立：{cond['field']} {cond['op']} {cond.get('value')!r}"
        return "未命中"

    # ── 性能自查（回归套件用）──────────────────────────────────────────
    def benchmark(self, facts: Dict[str, Any], iterations: int = 2000) -> Dict[str, float]:
        started = time.perf_counter()
        for _ in range(iterations):
            self.evaluate(facts)
        elapsed = time.perf_counter() - started
        return {
            "iterations": iterations,
            "totalSeconds": elapsed,
            "perCallMilliseconds": elapsed / max(1, iterations) * 1000.0,
        }


def _same_target(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    ka, kb = a.get("type"), b.get("type")
    if ka != kb:
        # set 与 halt 在"该不该继续"这件事上互相排斥
        return True
    if ka == "set":
        return a.get("path") == b.get("path")
    if ka == "retry":
        return a.get("experiment") == b.get("experiment")
    return True


def load_engine(path: str, *, strict: bool = False, min_confidence: float = 0.0) -> RuleEngine:
    """从目录/文件直接建引擎（loader.load_pack 的便捷封装）。"""
    from .loader import load_pack

    rules = load_pack(path)
    return RuleEngine(rules, strict=strict, min_confidence=min_confidence, source=str(path))


__all__ = [
    "RuleEngine",
    "Decision",
    "RuleMatch",
    "RuleConflict",
    "check_condition",
    "rule_matches",
    "resolve_fact",
    "load_engine",
]

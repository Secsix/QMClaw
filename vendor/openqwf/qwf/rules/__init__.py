"""`qwf.rules` —— L2「学到的规则」包（F1）。

与 DSL 主体刻意解耦：规则是**独立实体**，不进 IR、不进 `.qwf` 文本。理由见
`docs/DSL_DESIGN.md` 的 F1 条目 —— 混进语言会破坏"IR 静态可查"。

    from qwf.rules import RuleEngine, load_pack

    engine = RuleEngine(load_pack("qwf/rules/examples"), strict=True)
    decision = engine.evaluate({"experiment": "t1", "metrics.t1_us": 12.0})
    if decision is None:
        pass          # 没有规则命中 ⇒ 交给 L3（LLM）
    else:
        decision.action, decision.rule_id, decision.confidence

模块分工：

    schema.json   规则的权威形状（人/编辑器看）
    loader.py     读取 + 手写结构校验（不依赖 jsonschema）
    conflicts.py  六类冲突检测（加载期就能拦住"加了新规则撞了旧规则"）
    engine.py     求值、排序、解释、严格模式
"""
from __future__ import annotations

from .conflicts import (
    errors_of,
    find_conflicts,
    format_conflicts,
    raise_if_errors,
    trigger_satisfiable,
    triggers_overlap,
)
from .engine import (
    Decision,
    RuleConflict,
    RuleEngine,
    RuleMatch,
    check_condition,
    load_engine,
    resolve_fact,
    rule_matches,
)
from .loader import (
    SCHEMA_ID,
    SCHEMA_PATH,
    RuleError,
    load_pack,
    load_rule_file,
    load_schema,
    rules_to_dict,
    validate_rule,
)

__all__ = [
    "SCHEMA_ID",
    "SCHEMA_PATH",
    "Decision",
    "RuleConflict",
    "RuleEngine",
    "RuleError",
    "RuleMatch",
    "check_condition",
    "errors_of",
    "find_conflicts",
    "format_conflicts",
    "load_engine",
    "load_pack",
    "load_rule_file",
    "load_schema",
    "raise_if_errors",
    "resolve_fact",
    "rule_matches",
    "rules_to_dict",
    "trigger_satisfiable",
    "triggers_overlap",
    "validate_rule",
]

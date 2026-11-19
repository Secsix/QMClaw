"""规则包加载与结构校验（F1 / L2「学到的规则」）。

设计取向，与 DSL 主体一致：

- **纯离线**：不依赖 `jsonschema`（实验室机可能装不上）。`schema.json` 是给人和
  编辑器看的权威形状，`loader` 里有一份等价的手写校验 —— 两者由
  `tools/dsl_rules_selftest.py` 的 P 组交叉断言（schema.json 的 required 列表
  必须与 `_REQUIRED` 一致）。
- **不抛裸异常**：所有失败都是 `RuleError`，带 `code`（机器可判）与 `where`
  （文件 + 规则 id + 字段路径），可以直接进 CI 报告。
- **默认值集中在这里**：`version=1`、`priority`/`confidence` 必填不补 —— 缺失就是
  错误，因为"猜一个优先级"会让冲突检测失去意义。

规则文件形态（两种都支持）：

    单个对象   {"id": "...", "trigger": {...}, "action": {...}, ...}
    规则包     {"schema": "qwf.rules/1", "version": 1, "rules": [ ... ]}
    裸数组     [ {...}, {...} ]
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_ID = "qwf.rules/1"
SCHEMA_PATH = Path(__file__).with_name("schema.json")

_REQUIRED = ("id", "trigger", "action", "priority", "confidence")
_ALLOWED_TOP = {
    "id", "version", "description", "trigger", "action", "priority",
    "confidence", "tags", "scope", "provenance",
}
_OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "not_in", "contains", "exists", "matches"}
_ACTION_TYPES = {"set", "retry", "halt", "note", "branch"}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")


class RuleError(ValueError):
    """规则结构错误（带机器可判的 code）。"""

    def __init__(self, code: str, message: str, where: str = ""):
        self.code = code
        self.where = where
        super().__init__(f"[{code}] {message}" + (f"  ({where})" if where else ""))

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": str(self), "where": self.where}


# ── 单条校验 ────────────────────────────────────────────────────────────────
def validate_rule(raw: Any, where: str = "") -> Dict[str, Any]:
    """校验并规范化一条规则；失败抛 `RuleError`。"""
    if not isinstance(raw, dict):
        raise RuleError("E_RULE_SHAPE", f"规则必须是对象，收到 {type(raw).__name__}", where)

    rid = str(raw.get("id") or "")
    here = f"{where}:{rid}" if where else rid

    extra = set(raw) - _ALLOWED_TOP
    if extra:
        raise RuleError("E_RULE_UNKNOWN_FIELD",
                        f"未知字段 {sorted(extra)}；允许的字段见 schema.json", here)
    for key in _REQUIRED:
        if key not in raw:
            raise RuleError("E_RULE_MISSING", f"缺少必填字段 {key!r}", here)

    if not _ID_RE.match(rid):
        raise RuleError("E_RULE_ID", f"id 必须匹配 ^[a-z0-9][a-z0-9_-]{{2,63}}$，收到 {rid!r}", here)

    rule = dict(raw)
    rule["version"] = int(raw.get("version") or 1)
    if rule["version"] < 1:
        raise RuleError("E_RULE_VERSION", "version 必须 ≥ 1", here)

    prio = raw["priority"]
    if not isinstance(prio, int) or isinstance(prio, bool):
        raise RuleError("E_RULE_PRIORITY", f"priority 必须是整数，收到 {prio!r}", here)
    if not 0 <= prio <= 100:
        raise RuleError("E_RULE_PRIORITY", f"priority 必须在 0..100，收到 {prio}", here)

    conf = raw["confidence"]
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        raise RuleError("E_RULE_CONFIDENCE", f"confidence 必须是数值，收到 {conf!r}", here)
    if not 0.0 <= float(conf) <= 1.0:
        raise RuleError("E_RULE_CONFIDENCE", f"confidence 必须在 0..1，收到 {conf}", here)
    rule["confidence"] = float(conf)

    rule["trigger"] = _validate_trigger(raw["trigger"], here)
    rule["action"] = _validate_action(raw["action"], here)

    tags = raw.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise RuleError("E_RULE_TAGS", "tags 必须是字符串数组", here)
    rule["tags"] = list(tags)

    scope = raw.get("scope") or {}
    if not isinstance(scope, dict):
        raise RuleError("E_RULE_SCOPE", "scope 必须是对象", here)
    for key, value in scope.items():
        if key not in ("qubits", "experiments"):
            raise RuleError("E_RULE_SCOPE", f"scope 只支持 qubits / experiments，收到 {key!r}", here)
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise RuleError("E_RULE_SCOPE", f"scope.{key} 必须是字符串数组", here)
    rule["scope"] = {k: list(v) for k, v in scope.items()}

    prov = raw.get("provenance") or {}
    if not isinstance(prov, dict):
        raise RuleError("E_RULE_PROVENANCE", "provenance 必须是对象", here)
    src = prov.get("source")
    if src is not None and src not in ("session", "paper", "manual", "expert"):
        raise RuleError("E_RULE_PROVENANCE",
                        f"provenance.source 只能是 session/paper/manual/expert，收到 {src!r}", here)
    if src == "session" and not prov.get("evidence"):
        raise RuleError("E_RULE_PROVENANCE",
                        "session 来源的规则必须给 provenance.evidence（L3→L2 提升要留证据）", here)
    rule["provenance"] = dict(prov)

    return rule


def _validate_trigger(trigger: Any, here: str) -> Dict[str, Any]:
    if not isinstance(trigger, dict):
        raise RuleError("E_TRIGGER_SHAPE", "trigger 必须是对象", here)
    extra = set(trigger) - {"always", "all", "any", "none"}
    if extra:
        raise RuleError("E_TRIGGER_UNKNOWN", f"trigger 未知键 {sorted(extra)}", here)

    out: Dict[str, Any] = {}
    if trigger.get("always"):
        out["always"] = True
    for key in ("all", "any", "none"):
        conds = trigger.get(key)
        if conds is None:
            continue
        if not isinstance(conds, list):
            raise RuleError("E_TRIGGER_SHAPE", f"trigger.{key} 必须是数组", here)
        out[key] = [_validate_condition(c, f"{here}.trigger.{key}[{i}]")
                    for i, c in enumerate(conds)]

    empty = not any(out.get(k) for k in ("always", "all", "any", "none"))
    if empty:
        raise RuleError("E_TRIGGER_EMPTY",
                        "空 trigger 会命中任何事实；要恒真请显式写 always=true", here)
    return out


def _validate_condition(cond: Any, where: str) -> Dict[str, Any]:
    if not isinstance(cond, dict):
        raise RuleError("E_COND_SHAPE", "条件必须是对象", where)
    extra = set(cond) - {"field", "op", "value", "note"}
    if extra:
        raise RuleError("E_COND_UNKNOWN", f"条件未知键 {sorted(extra)}", where)
    field = cond.get("field")
    if not isinstance(field, str) or not _FIELD_RE.match(field):
        raise RuleError("E_COND_FIELD", f"field 非法：{field!r}", where)
    op = cond.get("op")
    if op not in _OPS:
        raise RuleError("E_COND_OP", f"op 非法：{op!r}；允许 {sorted(_OPS)}", where)

    if op == "exists":
        return {"field": field, "op": op}
    if "value" not in cond:
        raise RuleError("E_COND_VALUE", f"op={op} 需要 value", where)
    value = cond["value"]

    if op in ("in", "not_in"):
        if not isinstance(value, list):
            raise RuleError("E_COND_VALUE", f"op={op} 的 value 必须是数组", where)
    elif op in ("<", "<=", ">", ">="):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RuleError("E_COND_VALUE", f"op={op} 的 value 必须是数值", where)
    elif op == "matches":
        if not isinstance(value, str):
            raise RuleError("E_COND_VALUE", "op=matches 的 value 必须是正则字符串", where)
        try:
            re.compile(value)
        except re.error as exc:
            raise RuleError("E_COND_VALUE", f"正则非法：{exc}", where) from exc

    out = {"field": field, "op": op, "value": value}
    if cond.get("note"):
        out["note"] = str(cond["note"])
    return out


def _validate_action(action: Any, here: str) -> Dict[str, Any]:
    if not isinstance(action, dict):
        raise RuleError("E_ACTION_SHAPE", "action 必须是对象", here)
    kind = action.get("type")
    if kind not in _ACTION_TYPES:
        raise RuleError("E_ACTION_TYPE", f"action.type 非法：{kind!r}；允许 {sorted(_ACTION_TYPES)}", here)

    allowed = {
        "set": {"type", "path", "value", "delta", "deltaPercent", "params", "reason"},
        "retry": {"type", "experiment", "params", "maxAttempts", "reason"},
        "halt": {"type", "reason"},
        "note": {"type", "text"},
        "branch": {"type", "choice", "options"},
    }[kind]
    extra = set(action) - allowed
    if extra:
        raise RuleError("E_ACTION_UNKNOWN", f"action.type={kind} 不接受字段 {sorted(extra)}", here)

    if kind == "set":
        path = action.get("path")
        if not isinstance(path, str) or not path:
            raise RuleError("E_ACTION_FIELD", "action.type=set 需要非空 path", here)
        modes = [k for k in ("value", "delta", "deltaPercent") if k in action]
        if not modes:
            raise RuleError("E_ACTION_FIELD",
                            "action.type=set 需要 value / delta / deltaPercent 之一", here)
        if len(modes) > 1:
            raise RuleError("E_ACTION_FIELD",
                            f"action.type=set 的 {modes} 互斥，只能给一个", here)
        if modes[0] in ("delta", "deltaPercent"):
            amount = action[modes[0]]
            if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                raise RuleError("E_ACTION_FIELD", f"set.{modes[0]} 必须是数值", here)
        if "reason" in action and not isinstance(action["reason"], str):
            raise RuleError("E_ACTION_FIELD", "action.reason 必须是字符串", here)
    elif kind == "retry":
        if not isinstance(action.get("experiment"), str) or not action["experiment"]:
            raise RuleError("E_ACTION_FIELD", "action.type=retry 需要 experiment", here)
        attempts = action.get("maxAttempts", 1)
        if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 10:
            raise RuleError("E_ACTION_FIELD", "retry.maxAttempts 必须是 1..10 的整数", here)
        action = dict(action, maxAttempts=attempts)
    elif kind == "halt":
        if not isinstance(action.get("reason"), str) or not action["reason"]:
            raise RuleError("E_ACTION_FIELD", "action.type=halt 需要 reason", here)
    elif kind == "note":
        if not isinstance(action.get("text"), str) or not action["text"]:
            raise RuleError("E_ACTION_FIELD", "action.type=note 需要 text", here)
    elif kind == "branch":
        if not isinstance(action.get("choice"), str) or not action["choice"]:
            raise RuleError("E_ACTION_FIELD", "action.type=branch 需要 choice", here)
        options = action.get("options") or []
        if not isinstance(options, list) or any(not isinstance(o, str) for o in options):
            raise RuleError("E_ACTION_FIELD", "branch.options 必须是字符串数组", here)

    out = dict(action)
    if "params" in out and not isinstance(out["params"], dict):
        raise RuleError("E_ACTION_FIELD", "action.params 必须是对象", here)
    return out


# ── 文件 / 目录加载 ─────────────────────────────────────────────────────────
def load_rule_file(path: str | Path) -> List[Dict[str, Any]]:
    """读一个 .json 规则文件，返回规则列表（单条 / 规则包 / 裸数组都可）。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleError("E_READ", f"读不到规则文件：{exc}", str(p)) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuleError("E_JSON", f"JSON 解析失败：{exc}", str(p)) from exc

    if isinstance(data, list):
        raws = data
    elif isinstance(data, dict) and "rules" in data:
        meta_schema = data.get("schema")
        if meta_schema is not None and meta_schema != SCHEMA_ID:
            raise RuleError("E_SCHEMA_ID",
                            f"规则包 schema 应为 {SCHEMA_ID}，收到 {meta_schema!r}", str(p))
        raws = data.get("rules") or []
        if not isinstance(raws, list):
            raise RuleError("E_PACK_SHAPE", "rules 必须是数组", str(p))
    elif isinstance(data, dict):
        raws = [data]
    else:
        raise RuleError("E_PACK_SHAPE", f"不认识的顶层类型：{type(data).__name__}", str(p))

    return [validate_rule(r, f"{p.name}") for r in raws]


def load_pack(path: str | Path, *, recursive: bool = False) -> List[Dict[str, Any]]:
    """加载一个目录（或单文件）里的全部规则，按 id 去重并**拒绝重复 id**。"""
    p = Path(path)
    files: Iterable[Path]
    if p.is_dir():
        pattern = "**/*.json" if recursive else "*.json"
        files = sorted(f for f in p.glob(pattern) if f.name != "schema.json")
    else:
        files = [p]

    rules: List[Dict[str, Any]] = []
    for f in files:
        rules.extend(load_rule_file(f))

    seen: Dict[str, str] = {}
    for rule in rules:
        rid = rule["id"]
        if rid in seen:
            raise RuleError("E_DUP_ID", f"规则 id 重复：{rid!r}（已出现在 {seen[rid]}）", rid)
        seen[rid] = rid
    return rules


def rules_to_dict(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """打包成可落盘的规则包（带 schema 标记与版本）。"""
    return {"schema": SCHEMA_ID, "version": 1, "rules": list(rules)}


def load_schema() -> Dict[str, Any]:
    """读 `schema.json`（给"schema 与 loader 是否一致"的断言用）。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_required() -> Tuple[str, ...]:
    schema = load_schema()
    return tuple(schema.get("required") or ())


__all__ = [
    "SCHEMA_ID",
    "SCHEMA_PATH",
    "RuleError",
    "validate_rule",
    "load_rule_file",
    "load_pack",
    "rules_to_dict",
    "load_schema",
    "schema_required",
]

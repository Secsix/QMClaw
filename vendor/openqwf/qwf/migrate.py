"""dsl.migrate - IR 版本迁移（v1 → v2）。

为什么需要它
------------
v2 的判定标准只有一条：**IR 里不出现任何厂商符号**
（函数名、厂商参数名、厂商 Python 表达式——见 `docs/BACKEND_ADAPTERS.md` §0）。
而 v1 IR 满地都是 `sq.t1`、`qter.fitData(...)`、`$a1.T1_us`。手工改写既慢又容易改错，
所以这里提供**可复现的自动迁移**：v1 IR → v2 IR，厂商符号逐条落到语义位置上。

迁移映射（对照 `docs/BACKEND_ADAPTERS.md` §3.2）
------------------------------------------------
| v1 | v2 |
|---|---|
| `measure {fn, qubit, args}` | `run {experiment, qubit, params, sweep, analysis}` |
| `analyze {dataset, command}` | 并入上游 `run` 的 `analysis{fit}`；绑定名改指该 run |
| `$a1.T1_us`（a1 是 analyze 绑定） | `$c1.quantities.T1`（c1 是 run 绑定） |
| `assert threshold 20` | `threshold {value, unit}`（数量带单位，值按量纲归一） |

**刻意不做**的事
----------------
* 不改厂商参数名（`amp` → `drive_amp` 之类）：没有参数名注册表，猜错比不改更糟；
  迁移会给出 `W_MIGRATION_NOTE` 提示这类残留，交给后端适配器或人处理。
* 不动 `if` / `repeat` / `call` 等控制流与 `let` / `assert` / `say` 的语义：
  它们本来就不含厂商符号。

迁移**不保证可执行**：v2 的 `run` op 目前只有结构校验与能力预检，
执行器仍然只认 v1 的 `measure`（见 `dsl/executor.py`）。本模块的产物用于
"方言边界演练 + 能力预检"，要真跑还得等执行器补 v2 分支。
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from .const import (
    IR_VERSION,
    IR_VERSION_V2,
    OP_ANALYZE,
    OP_MEASURE,
    OP_RUN,
    OP_SAY,
    SUPPORTED_IR_VERSIONS,
)
from .quantities import LEGACY_METRICS, legacy_to_quantity

# 实验种类 → 拟合种类（只收有把握的；推不出来就留空并给提示）
FIT_BY_EXPERIMENT: Dict[str, str] = {
    "t1": "t1",
    "t2": "t2",
    "ramsey": "ramsey",
    "ramsey_df": "ramsey",
    "ramsey_t2": "t2star",
    "spinecho_t2": "t2echo",
    "spectroscopy": "spectroscopy",
    "spectroscopy_auto": "spectroscopy",
    "xeb": "xeb",
    "iqraw": "iq_discriminate",
}

# 迁移要保留的通用字段（其余字段原样搬运）
_PASSTHROUGH = ("as", "when", "onError", "retries", "retryDelay", "timeout",
                "effect", "note", "export", "source")

_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)")
# 花括号插值形态：`${a1.T1_us}`（say.template / 文本插值用）
_REF_BRACE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)\}")


def _diag(code: str, message: str, path: str = "", level: str = "warning") -> Dict[str, Any]:
    return {"code": code, "level": level, "message": message, "path": path,
            "line": None, "col": None}


# ── 小工具 ────────────────────────────────────────────────────────────────────

def experiment_kind(fn: str) -> str:
    """`sq.t1` → `t1`（去掉厂商命名空间）；没有点就原样返回。"""
    text = str(fn or "")
    return text.split(".")[-1] if "." in text else text


def _arange_points(values: List[Any]) -> Optional[int]:
    """`{"$arange": [start, stop, step]}` 的点数（与 np.arange 一致）。"""
    try:
        start, stop, step = float(values[0]), float(values[1]), float(values[2])
    except (TypeError, ValueError, IndexError):
        return None
    if step == 0:
        return None
    return max(1, int(math.ceil(abs(stop - start) / step)))


def _split_args(args: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """把 v1 `args` 拆成 v2 的 `params` 与 `sweep`。

    扫描参数（`{"$arange": [...]}` / `{"range": [...]}`）进 `sweep`，
    其余原样进 `params`（参数名仍是厂商名，迁移只标注不改名）。
    """
    params: Dict[str, Any] = {}
    sweep: Dict[str, Any] = {}
    notes: List[Dict[str, Any]] = []
    for key, value in (args or {}).items():
        if isinstance(value, dict) and ("$arange" in value or "range" in value):
            raw = value.get("$arange") or value.get("range") or []
            points = _arange_points(raw) if len(raw) >= 3 else None
            if points is None and len(raw) == 3:
                points = None
            axis = {"start": raw[0] if len(raw) > 0 else None,
                    "stop": raw[1] if len(raw) > 1 else None,
                    "points": points}
            sweep[key] = axis
            if points is None:
                notes.append(_diag("W_MIGRATION_NOTE",
                                   f"扫描参数 {key!r} 的区间无法折算成 points，原样保留",
                                   f"params.{key}"))
            continue
        params[key] = value
    return params, sweep, notes


def _rewrite_refs(node: Any, alias: Dict[str, str],
                  notes: Optional[List[Dict[str, Any]]] = None) -> Any:
    """把 `$a1.xxx` / `${a1.xxx}` 里的绑定名按 alias 改写（`a1` → `c1`），递归进 dict/list。"""
    if isinstance(node, dict):
        return {k: _rewrite_refs(v, alias, notes) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite_refs(v, alias, notes) for v in node]
    if isinstance(node, str) and "$" in node:

        def _mapped(name: str, rest: str, path: str) -> str:
            new_name = alias.get(name, name)
            if rest.startswith(".dataset") or not rest:
                return "$" + new_name + rest
            head = rest[1:].split(".")[0]
            entry = LEGACY_METRICS.get(head)
            if entry and not rest.startswith(".metrics"):
                # v1 扁平指标 `$a1.T1_us` → v2 数量 `$c1.quantities.T1`
                return f"${new_name}.quantities.{entry[0]}"
            if entry is None and notes is not None and name in alias:
                notes.append(_diag(
                    "W_MIGRATION_NOTE",
                    f"指标 {head!r} 没有量纲映射，迁移后按原名保留（{new_name}.{rest[1:]}）",
                    path))
            return "$" + new_name + rest

        def repl(match: "re.Match[str]") -> str:
            return _mapped(match.group(1), match.group(2), "ref")

        def repl_brace(match: "re.Match[str]") -> str:
            name, rest = match.group(1), match.group(2)
            mapped = _mapped(name, rest, "ref")
            if mapped.startswith("$"):
                mapped = mapped[1:]
            return "${" + mapped + "}"

        node = _REF_BRACE_RE.sub(repl_brace, node)
        return _REF_RE.sub(repl, node)
    return node


def _quantity_threshold(metric: Any, threshold: Any) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """把数值阈值改成 `{value, unit}`（按指标量纲归一）；不能折算就原样返回。"""
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        return threshold, None
    if not isinstance(metric, str):
        return threshold, None
    match = _REF_RE.search(metric)
    if not match:
        return threshold, None
    tail = match.group(2)[1:].split(".")[0] if match.group(2) else ""
    entry = LEGACY_METRICS.get(tail)
    if not entry:
        return threshold, None
    quantity = legacy_to_quantity(tail, float(threshold))
    if quantity is None:
        return threshold, None
    return quantity, quantity


# ── 主体迁移 ──────────────────────────────────────────────────────────────────

def _collect_statements(stmts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for st in stmts or []:
        if not isinstance(st, dict):
            continue
        out.append(st)
        for key in ("body", "then", "else"):
            if isinstance(st.get(key), list):
                out.extend(_collect_statements(st[key]))
    return out


def _producer_map(stmts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """绑定名 → 产出数据集的语句（measure / run，且 reportDataset 不为 False）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for st in stmts or []:
        if not isinstance(st, dict):
            continue
        if st.get("op") in (OP_MEASURE, OP_RUN) and st.get("reportDataset", True) is not False:
            name = st.get("as")
            if isinstance(name, str):
                out[name] = st
    return out


def migrate_v1_to_v2(ir: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """把 v1 IR 迁到 v2 IR。返回 (ir_v2, notes)。

    `notes` 里是迁移说明：能自动改的都改了，改不动的地方给 `W_MIGRATION_NOTE`
    （级别 warning），不会静默丢信息。
    """
    out = copy.deepcopy(ir)
    notes: List[Dict[str, Any]] = []
    if not isinstance(out, dict):
        return out, [_diag("E_MIGRATION_UNSUPPORTED", "IR 不是对象", "", level="error")]

    version = out.get("ir_version") or IR_VERSION
    if version >= IR_VERSION_V2:
        return out, [_diag("E_MIGRATION_UNSUPPORTED",
                           f"ir_version={version!r} 已经是 v2 或更高，没有 v1→v2 迁移路径", "",
                           level="error")]

    out["ir_version"] = IR_VERSION_V2

    # 1) measure → run（先做，后面 analyze 要认它的产物）
    all_stmts = _collect_statements(out.get("pipeline") or [])
    for st in all_stmts:
        if st.get("op") != OP_MEASURE:
            continue
        fn = str(st.get("fn") or "")
        kind = experiment_kind(fn)
        params, sweep, sweep_notes = _split_args(st.get("args") or st.get("params") or {})
        notes.extend(sweep_notes)
        new_stmt: Dict[str, Any] = {"op": OP_RUN, "experiment": kind}
        for key in _PASSTHROUGH:
            if key in st:
                new_stmt[key] = st[key]
        if st.get("qubit") is not None:
            new_stmt["qubit"] = st["qubit"]
        if params:
            new_stmt["params"] = params
        if sweep:
            new_stmt["sweep"] = sweep
        if "reportDataset" in st:
            new_stmt["reportDataset"] = st["reportDataset"]
        fit = FIT_BY_EXPERIMENT.get(kind)
        if fit:
            new_stmt["analysis"] = {"fit": fit}
        st.clear()
        st.update(new_stmt)
        notes.append(_diag("W_MIGRATION_NOTE",
                           f"measure.fn={fn!r} → run.experiment={kind!r}"
                           + (f"（analysis.fit={fit!r}）" if fit else "（拟合种类推不出，analysis 留空）"),
                           "measure"))

    # 2) analyze → 并入上游 run 的 analysis{fit}；绑定名改指该 run
    producers = _producer_map(out.get("pipeline") or [])
    alias: Dict[str, str] = {}
    dropped: List[int] = []

    def _handle_analyze(stmts: List[Dict[str, Any]]) -> None:
        for idx, st in enumerate(stmts or []):
            if not isinstance(st, dict):
                continue
            if st.get("op") == OP_ANALYZE:
                dataset = st.get("dataset")
                src_name = None
                if isinstance(dataset, str):
                    match = _REF_RE.search(dataset)
                    if match:
                        src_name = match.group(1)
                producer = producers.get(src_name) if src_name else None
                fit = None
                if producer is not None:
                    fit = FIT_BY_EXPERIMENT.get(str(producer.get("experiment") or ""))
                    analysis = producer.get("analysis") or {}
                    if fit and not analysis.get("fit"):
                        producer["analysis"] = {**analysis, "fit": fit}
                analyze_name = st.get("as")
                if producer is not None and isinstance(analyze_name, str):
                    alias[analyze_name] = producer.get("as")
                    dropped.append(id(st))
                elif fit:
                    st["fit"] = fit
                    st.pop("command", None)
                else:
                    notes.append(_diag(
                        "W_MIGRATION_NOTE",
                        f"analyze({st.get('as')!r}) 找不到产出数据集的 run，"
                        "厂商命令无法自动折算成 fit（请人工确认）",
                        "analyze"))
                    st.pop("command", None)
                continue
            for key in ("body", "then", "else"):
                if isinstance(st.get(key), list):
                    _handle_analyze(st[key])

    _handle_analyze(out.get("pipeline") or [])

    # 3) 删掉已并入 run 的 analyze 语句
    def _prune(stmts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        keep: List[Dict[str, Any]] = []
        for st in stmts or []:
            if not isinstance(st, dict):
                continue
            if id(st) in dropped:
                continue
            for key in ("body", "then", "else"):
                if isinstance(st.get(key), list):
                    st[key] = _prune(st[key])
            keep.append(st)
        return keep

    out["pipeline"] = _prune(out.get("pipeline") or [])

    # 4) assert 阈值带单位（必须在引用改写之前做：阈值折算认的是 v1 的旧指标名）
    for st in _collect_statements(out.get("pipeline") or []):
        if st.get("op") != "assert":
            continue
        metric = st.get("metric")
        for key in ("threshold", "value"):
            if key not in st:
                continue
            new_value, _ = _quantity_threshold(metric, st[key])
            if new_value is not st[key]:
                st[key] = new_value

    # 5) 引用改写（绑定别名 + v1 指标 → v2 数量）
    if alias:
        out = _rewrite_refs(out, alias, notes)
        notes.append(_diag("W_MIGRATION_NOTE",
                           f"分析绑定改指实验绑定：{', '.join(f'{k}→{v}' for k, v in alias.items())}",
                           "pipeline"))

    # 6) 推导能力需求
    caps: List[str] = []
    for st in _collect_statements(out.get("pipeline") or []):
        if st.get("op") != OP_RUN:
            continue
        kind = str(st.get("experiment") or "")
        cap = f"experiment.{kind}"
        if cap not in caps:
            caps.append(cap)
        analysis = st.get("analysis") or {}
        fit = analysis.get("fit") if isinstance(analysis, dict) else None
        if fit:
            fcap = f"fit.{fit}"
            if fcap not in caps:
                caps.append(fcap)
    requirements = dict(out.get("requirements") or {})
    existing = list(requirements.get("capabilities") or [])
    merged = existing + [c for c in caps if c not in existing]
    if merged:
        requirements["capabilities"] = merged
        out["requirements"] = requirements

    # 7) 迁移元信息
    meta = dict(out.get("meta") or {})
    meta["migratedFrom"] = f"v{version}"
    meta["migratedTo"] = f"v{IR_VERSION_V2}"
    out["meta"] = meta

    if not caps:
        notes.append(_diag("W_MIGRATION_NOTE", "IR 里没有 run 语句，能力需求为空", "requirements"))
    return out, notes


MIGRATIONS: Dict[Tuple[int, int], Any] = {
    (1, 2): migrate_v1_to_v2,
}


def migrate(ir: Dict[str, Any], target_version: int = IR_VERSION_V2) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """把 IR 迁到目标版本。返回 (ir_target, notes)。

    * 版本已经够新 → 原样返回（只补一条说明）。
    * 没有迁移路径 → 原样返回 + `E_MIGRATION_UNSUPPORTED`（error 级）。
    """
    if not isinstance(ir, dict):
        return ir, [_diag("E_MIGRATION_UNSUPPORTED", "IR 不是对象", "", level="error")]
    version = ir.get("ir_version") or IR_VERSION
    if version == target_version:
        return copy.deepcopy(ir), [_diag("W_MIGRATION_NOTE",
                                        f"ir_version 已是 {target_version}，无需迁移", "")]
    if version not in SUPPORTED_IR_VERSIONS or target_version not in SUPPORTED_IR_VERSIONS:
        return copy.deepcopy(ir), [_diag(
            "E_MIGRATION_UNSUPPORTED",
            f"没有从 v{version} 到 v{target_version} 的迁移路径"
            f"（支持的版本：{', '.join(str(v) for v in SUPPORTED_IR_VERSIONS)}）", "", level="error")]
    fn = MIGRATIONS.get((version, target_version))
    if fn is None:
        return copy.deepcopy(ir), [_diag(
            "E_MIGRATION_UNSUPPORTED",
            f"没有从 v{version} 到 v{target_version} 的迁移路径", "", level="error")]
    return fn(ir)

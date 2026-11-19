"""D1：数据可达性静态检查（dataflow reachability）。

对应 V2_PLAN 的 D1 与突变用例 **M30**（"measure 关掉 reportDataset，下游仍读 dataset"）：
v1 的执行器里 `reportDataset=false` 会让实验代码不再带数据集探针（见
`adapters/lqcs.py:500`），于是这条实验**产不出数据集**，但 IR 里没有任何地方
拦住"下游继续读 `$x.dataset`"——运行期只会读到空值，属于静默失败。

本模块给出两条诊断：

* `E_DATA_UNREACHABLE`（error）：产不出数据集的语句，下游却还要读它的数据集。
* `W_DEAD_DATA`（warning）：产出了数据集，但下游从不读它（多半是白跑的真机实验）。

约定：
* `E_DATA_UNREACHABLE` 对 v1 / v2 都生效（它挡的是真实缺陷，不是风格问题）。
* `W_DEAD_DATA` 只对 v2 IR 生效（v1 存量示例里有不少"跑了但没读"的实验，
  全量报会变成噪声，破坏 v1 行为不变的前提）。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .const import IR_VERSION, IR_VERSION_V2, OP_MEASURE, OP_READ, OP_RUN

# 会读数据集的字段后缀
_DATA_FIELDS = ("dataset", "datasets")

# 引用形态：`$name.dataset` / `${name.dataset}`
_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)")
_REF_BRACE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)\}")

# 产出数据集的语句（会绑定一个名字）
_PRODUCER_OPS = (OP_MEASURE, OP_RUN)


def _diag(code: str, message: str, path: str = "",
          line: Optional[int] = None, col: Optional[int] = None,
          level: str = "error") -> Dict[str, Any]:
    return {"code": code, "level": level, "message": message, "path": path,
            "line": line, "col": col}


def _source_of(st: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    src = st.get("source") or {}
    if not isinstance(src, dict):
        return None, None
    return src.get("line"), src.get("col")


def _iter_strings(node: Any):
    """递归吐出字符串叶子（IR 里引用都藏在字符串里）。"""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_strings(value)


def _refs_in(text: str) -> List[Tuple[str, str]]:
    """文本里的引用 → [(绑定名, 字段路径)]（字段路径以 "." 开头）。"""
    out: List[Tuple[str, str]] = []
    for match in _REF_BRACE_RE.finditer(text):
        out.append((match.group(1), match.group(2)))
    for match in _REF_RE.finditer(text):
        out.append((match.group(1), match.group(2)))
    return out


def _walk(stmts: List[Dict[str, Any]], path: str) -> List[Tuple[str, Dict[str, Any], str]]:
    """产出 [(路径, 语句, 路径前缀)]，含控制流子块。"""
    out: List[Tuple[str, Dict[str, Any], str]] = []
    for idx, st in enumerate(stmts or []):
        if not isinstance(st, dict):
            continue
        here = f"{path}[{idx}]"
        out.append((here, st, path))
        for key in ("body", "then", "else"):
            if isinstance(st.get(key), list):
                out.extend(_walk(st[key], f"{here}.{key}"))
    return out


def dataflow_diags(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    """数据可达性检查。返回诊断列表（顺序稳定，便于回归比对）。"""
    if not isinstance(ir, dict):
        return []
    version = ir.get("ir_version") or IR_VERSION
    statements = _walk(ir.get("pipeline") or [], "pipeline")
    if not statements:
        return []

    # 1) 谁产出数据集
    producers: Dict[str, Dict[str, Any]] = {}
    for path, st, _ in statements:
        if st.get("op") not in _PRODUCER_OPS:
            continue
        name = st.get("as")
        if not isinstance(name, str) or not name:
            continue
        producers[name] = {
            "path": path,
            "stmt": st,
            "produces_dataset": st.get("reportDataset", True) is not False,
        }

    if not producers:
        return []

    # 2) 谁读了谁
    reads: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (被读名, 字段, 路径, 语句)
    for path, st, _ in statements:
        for text in _iter_strings(st):
            for name, field in _refs_in(text):
                if name in producers:
                    reads.append((name, field, path, st))

    diags: List[Dict[str, Any]] = []

    # 3) 产不出数据集却被下游读 ⇒ 静态拒绝（M30）
    reported: set = set()
    for name, field, path, st in reads:
        producer = producers[name]
        if producer["produces_dataset"]:
            continue
        if not any(field == f".{suffix}" or field.startswith(f".{suffix}.")
                   for suffix in _DATA_FIELDS):
            continue
        if name in reported:
            continue
        reported.add(name)
        line, col = _source_of(st)
        diags.append(_diag(
            "E_DATA_UNREACHABLE",
            f"${name}.dataset 不可达：产出它的语句（{producer['path']}）设置了 "
            f"reportDataset=false（不会产出数据集），但下游 {path} 仍要读它的数据集",
            path, line, col, level="error"))

    # 4) 产了数据集却没人读 ⇒ warning（只对 v2 生效）
    if version >= IR_VERSION_V2:
        used = {name for name, _field, _path, _st in reads}
        for name, producer in producers.items():
            if not producer["produces_dataset"]:
                continue
            if name in used:
                continue
            line, col = _source_of(producer["stmt"])
            diags.append(_diag(
                "W_DEAD_DATA",
                f"${name}.dataset 无人读取：产出它的语句跑完后下游不再引用它的数据集"
                "（若确实只为副作用，可显式说明）",
                producer["path"], line, col, level="warning"))

    return diags

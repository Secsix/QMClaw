"""实验词表读取器（**唯一真源 = 词表 JSON**，核心层不硬编码任何厂商函数名）。

为什么要有这一层：多比特支持之前，"这个实验要几个比特"这件事**没人声明**，
所以 `sq.swap` 传 1 个比特也照跑不误（结果还是错的）。要拦在下发之前，
就必须先有一张**两边都能读**的表。

* 词表文件：由 `qwf.paths.vocab_path()` 决定
  （`QWF_VOCAB_PATH` → 否则 `<config_dir>/experiment_configs.json`）；
* schema：

      {
        "experiments": {
          "<名字>": {"function": "sq.xxx", "qubits": 1, "available": true}
        },
        "vendor_capabilities": {
          "two_qubit": {"equivalent_methods": {"sq.swap": "swap11to20"}}
        }
      }

**核心层不写死厂商函数名**：词表内容一律从 JSON 读，`qwf/lint.py` 里遗留的
`SQ_NODE_FNS` 硬编码表是历史包袱，新代码不要照抄。

**读不到文件时不抛异常**：词表为空，`qubit_arity()` 返回 `None`、
`is_known()` 返回 `False` —— 上游独立使用（没有 `config/`）也不会炸。

用法（与下游 `services.workflow_service.dsl.experiments` 同名同义）：

    from .vocabulary import qubit_arity, experiment_names, vocabulary_fns
    qubit_arity("sq.swap")        # 2
    qubit_arity("sq.ramsey_df")   # 1（词表名是 ramsey，函数名是 sq.ramsey_df）
    qubit_arity("sq.whatever")    # None —— 词表里没有 → 调用方按"未知"处理，不拦
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .paths import vocab_path

#: 词表读得到、但**某个实验没登记**时的兜底元数。
#: （词表整体读不到 → 严格降级，一律返回 None，不猜。）
_FALLBACK_ARITY: Dict[str, int] = {"swap": 2, "drag_calibrate": 2}

#: 进程内缓存：{"path": str, "mtime": float, "experiments": dict, "raw": dict}
_CACHE: Dict[str, Any] = {}


def _read_json(path: str) -> Dict[str, Any]:
    """读 JSON；任何异常都吞掉（返回空字典）。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> Dict[str, Dict[str, Any]]:
    """读词表（按路径 + mtime 缓存：改了 JSON 能自动生效，不用重启）。"""
    path = vocab_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    if _CACHE.get("path") == path and _CACHE.get("mtime") == mtime:
        return _CACHE.get("experiments") or {}

    raw = _read_json(path)
    experiments: Dict[str, Dict[str, Any]] = {}
    block = raw.get("experiments")
    if isinstance(block, dict):
        for name, spec in block.items():
            if isinstance(spec, dict):
                experiments[str(name)] = spec
    _CACHE.update({"path": path, "mtime": mtime, "experiments": experiments, "raw": raw})
    return experiments


def reload() -> None:
    """清掉进程内缓存（改了词表文件或环境变量后可强制重读）。"""
    _CACHE.clear()


def _bare(fn: str) -> str:
    """`sq.t1` → `t1`；`t1` → `t1`。"""
    name = str(fn or "").strip()
    if name.startswith("sq."):
        name = name[3:]
    return name


def _lookup(name: str) -> Optional[Dict[str, Any]]:
    """按名字找词表条目。

    查法（与下游一致）：先按**词表名**查，再按 `spec["function"]` 反查 ——
    词表名 `ramsey` 但函数名是 `sq.ramsey_df`，两种写法都要能查到。
    """
    key = str(name or "").strip()
    if not key:
        return None
    specs = _load()
    if not specs:
        return None
    bare = _bare(key)
    if key in specs:
        return specs[key]
    if bare in specs:
        return specs[bare]
    for spec in specs.values():
        fn = str(spec.get("function") or "").strip()
        if not fn:
            continue
        if fn == key or _bare(fn) == bare or fn.split(".")[-1] == bare:
            return spec
    return None


def experiment_names() -> List[str]:
    """词表里的全部实验名（排序）。"""
    return sorted(_load().keys())


def qubit_arity(fn: str) -> Optional[int]:
    """这个实验要几个比特。词表里没有 → `None`（**不拦**，交给后端自己报）。

    注意 `ramsey` 的函数名是 `sq.ramsey_df`、词表名是 `ramsey`，
    所以除了按函数名查，还要按**词表名**再查一次。
    """
    bare = _bare(fn)
    if not bare:
        return None
    specs = _load()
    spec = _lookup(fn)
    if spec is not None:
        value = spec.get("qubits")
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return int(value)
        if value is None:
            # 词表里没写元数 → 默认单比特
            return 1
    if specs:
        # 词表读得到、但这条没登记 → 兜底（别把双比特误判成单比特）
        return _FALLBACK_ARITY.get(bare)
    return None


def is_known(fn: str) -> bool:
    """这个实验在词表里吗（词表名或函数名命中即算）。"""
    return _lookup(fn) is not None


def vocabulary_fns() -> Dict[str, bool]:
    """词表里的全部实验函数 → 真机上是否可直接做（`available` 字段，默认 True）。

    `available=false` 的实验（如 `sq.swap`：**真机没有同名模块级函数**）
    不是"写错了"，而是"只能跑在 qiskit 这类模拟后端上" —— lint 要区别对待，
    不能一律报 E_UNKNOWN_FN。

    返回 `Dict[str, bool]`（下游 lint 用 `vocabulary_fns().get(fn)` 拿三态）。
    """
    out: Dict[str, bool] = {}
    for spec in _load().values():
        fn = str(spec.get("function") or "").strip()
        if fn:
            out[fn] = bool(spec.get("available", True))
    return out


def vocabulary_fn_names() -> List[str]:
    """词表里所有 `function` 值（排序后的列表）。"""
    return sorted(vocabulary_fns().keys())


def two_qubit_vendor() -> Dict[str, object]:
    """厂商的**双比特**能力登记（词表 `vendor_capabilities.two_qubit`）。

    真机不是"没有双比特"，而是双比特在另一个模块里：**类方法**形态，
    还要 2 比特 + 1 耦合器。当前 DSL/后端链只会拼 `sq.xxx(...)`，调不到它，
    但缺口要有个能查的地方 —— 这就是本函数的用途。
    """
    raw = _CACHE.get("raw")
    if not isinstance(raw, dict) or _CACHE.get("path") != vocab_path():
        raw = _read_json(vocab_path())
        _CACHE.update({"raw": raw})
    block = (raw.get("vendor_capabilities") or {}).get("two_qubit") or {}
    return dict(block) if isinstance(block, dict) else {}


def vendor_method_for(fn: str) -> Optional[str]:
    """这个词表里的实验，在厂商双比特模块里的**对应方法名**。

    例：`sq.swap` → `swap11to20`。用来在 lint 里指出"真机其实有这条路，
    但当前 DSL 调不到"。
    """
    table = two_qubit_vendor().get("equivalent_methods") or {}
    key = str(fn or "").strip()
    if not isinstance(table, dict) or not key:
        return None
    value = table.get(key)
    if isinstance(value, str) and value:
        return value
    bare = _bare(key)
    for name, mapped in table.items():
        if _bare(str(name)) == bare and isinstance(mapped, str) and mapped:
            return mapped
    return None


__all__ = [
    "experiment_names",
    "qubit_arity",
    "is_known",
    "vocabulary_fns",
    "vocabulary_fn_names",
    "two_qubit_vendor",
    "vendor_method_for",
    "reload",
]

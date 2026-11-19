"""dsl.experiments — 实验词表（**唯一真源 = config/experiment_configs.json**）。

为什么要有这一层：多比特支持之前，"这个实验要几个比特"这件事**没人声明**，
所以 `sq.swap` 传 1 个比特也照跑不误（结果还是错的）。现在要拦在下发之前，
就必须先有一张**两边都能读**的表：

* `config/experiment_configs.json` —— 真机/qiskit **共用**的实验词表
  （`qiskit_experiments.py` 里的 `coverage_report()` 就是拿它做覆盖对账的）；
* 每条实验声明 `qubits`（元数）：`swap` / `drag_calibrate` 是 2，其余是 1。

**为什么不放在代码里**：DSL 层（workflow_service）和量子层（quantum_service）
是两个微服务，谁都不该 import 谁；配置文件是它们本来就共用的东西。
（真机侧 `dsl/backends/lqcs.py` 的 `SQ_NODES` 是**厂商函数**清单，
和"元数"是两回事 —— `sq.swap` 这种**模块级**双比特函数在真机上不存在，
所以 `swap.available=false`；但真机**有**双比特能力，只是在另一个模块
`cz_nodes.DCZTuner` 里，是**类方法**、还要 2 比特 + 1 耦合器，
见 `config/experiment_configs.json` 的 `vendor_capabilities.two_qubit`
与 `two_qubit_vendor()`。）

用法：

    from .experiments import qubit_arity, experiment_names
    qubit_arity("sq.swap")      # 2
    qubit_arity("sq.t1")        # 1
    qubit_arity("sq.whatever")  # None —— 词表里没有 → 调用方按"未知"处理，不拦
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

#: 词表文件：<server>/config/experiment_configs.json
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "experiment_configs.json"

#: 解析不出来时的兜底元数（词表读不到时，至少让双比特实验不被误判成单比特）
_FALLBACK_ARITY: Dict[str, int] = {"swap": 2, "drag_calibrate": 2}

_cache: Dict[str, object] = {}


def _load() -> Dict[str, Dict[str, object]]:
    """读词表；按 mtime 缓存（改了 config 能自动生效，不用重启）。"""
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _cache.get("mtime") == mtime and isinstance(_cache.get("data"), dict):
        return _cache["data"]  # type: ignore[return-value]
    data: Dict[str, Dict[str, object]] = {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = (json.load(f) or {}).get("experiments") or {}
        for name, spec in raw.items():
            if isinstance(spec, dict):
                data[str(name)] = spec
    except Exception:
        data = {}
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def experiment_names() -> List[str]:
    """词表里的全部实验名。"""
    return sorted(_load().keys())


def _bare(fn: str) -> str:
    """`sq.t1` → `t1`；`t1` → `t1`。"""
    name = str(fn or "").strip()
    if name.startswith("sq."):
        name = name[3:]
    return name


def qubit_arity(fn: str) -> Optional[int]:
    """这个实验要几个比特。词表里没有 → `None`（**不拦**，交给后端自己报）。

    注意 `ramsey` 的函数名是 `sq.ramsey_df`、词表名是 `ramsey`，
    所以除了按函数名查，还要按**词表名**再查一次。
    """
    bare = _bare(fn)
    if not bare:
        return None
    specs = _load()
    spec = specs.get(bare)
    if spec is None:
        # 函数名与词表名不同的情况：按词表里的 function 反查
        for name, item in specs.items():
            if str(item.get("function") or "").split(".")[-1] == bare:
                spec = item
                bare = name
                break
    if spec is not None:
        value = spec.get("qubits")
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return int(value)
    return _FALLBACK_ARITY.get(bare)


def is_known(fn: str) -> bool:
    """这个实验在词表里吗。"""
    return qubit_arity(fn) is not None


def vocabulary_fns() -> Dict[str, bool]:
    """词表里的全部实验函数 → 真机上是否可直接做（`available` 字段）。

    `available=false` 的实验（如 `sq.swap`：**真机 sq_nodes 里没有双比特函数**）
    不是"写错了"，而是"只能跑在 qiskit 这类模拟后端上" —— lint 要区别对待，
    不能一律报 E_UNKNOWN_FN。
    """
    out: Dict[str, bool] = {}
    for _name, spec in _load().items():
        fn = str(spec.get("function") or "").strip()
        if fn:
            out[fn] = bool(spec.get("available", True))
    return out


def two_qubit_vendor() -> Dict[str, object]:
    """厂商的**双比特**能力登记（`config/experiment_configs.json` 的
    `vendor_capabilities.two_qubit`）。

    真机不是"没有双比特"，而是双比特在 `cz_nodes.DCZTuner` 里：
    **类方法**形态，且要 **2 比特 + 1 耦合器**（`qubits[2]` 是 coupler）。
    当前 DSL/后端链调不到它（只会拼 `sq.xxx(...)`），
    但缺口要有个能查的地方 —— 这就是本函数的用途。
    """
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
        block = (raw.get("vendor_capabilities") or {}).get("two_qubit") or {}
        return dict(block) if isinstance(block, dict) else {}
    except Exception:
        return {}


def vendor_method_for(fn: str) -> Optional[str]:
    """这个词表里的实验，在厂商双比特模块里的**对应方法名**。

    例：`sq.swap` → `swap11to20`（`cz_nodes.DCZTuner` 的方法）。
    用来在 lint 里指出"真机其实有这条路，但当前 DSL 调不到"。
    """
    table = two_qubit_vendor().get("equivalent_methods") or {}
    if isinstance(table, dict):
        value = table.get(str(fn or "").strip())
        if isinstance(value, str) and value:
            return value
    return None

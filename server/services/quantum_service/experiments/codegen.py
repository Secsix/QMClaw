"""把 ``kind`` + 参数拼成后端能执行的 vendor 调用代码。

这段逻辑**只允许存在于测控微服务内部**。技能 / 智能体 / 前端永远不该看到
``sq.t1('q0', zpa=...)`` 这种字符串 —— 一旦它们能拼代码，就意味着它们知道
"哪个实验函数、参数叫 f10 还是 freq、要不要耦合器"，换后端或改参数口径时
整个生态都要跟着改。

实现策略：

1. 优先复用上游成熟实现 ``vendor/OpenQWF/adapters/lqcs.build_experiment_code``
   （它已经处理了多比特、类调用形态的 ``cz.dcz``、裸比特标识符、数据集探针）；
2. 上游不可用时退回本文件的极简实现（保证测控服务自己不依赖工作流服务也能跑）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

SAFE_ARGNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_QUBIT_RE = re.compile(r"^[A-Za-z0-9_%\-]+$")

#: ``vendor`` = 用上游实现；``local`` = 用本文件兜底
CODER = "local"
_VENDOR_BUILD = None


def _vendor_builder():
    """延迟取上游实现（导入失败不能拖垮测控服务）。"""
    global _VENDOR_BUILD, CODER
    if _VENDOR_BUILD is None:
        try:
            from services.workflow_service.dsl.backends.lqcs import (
                build_experiment_code as _build,
            )
            _VENDOR_BUILD = _build
            CODER = "vendor"
        except Exception:
            _VENDOR_BUILD = False
            CODER = "local"
    return _VENDOR_BUILD or None


# 参数别名：语义名 → vendor 关键字名
_KWARG_ALIASES = {
    "freq_range": "freq",
    "detuning_range": "detuning",
    "amp_range": "amp",
    "drag_range": "drag",
    "power_range": "power",
}

# 这些关键字在真机上习惯按"扫描区间"传，值可以写成 [start, stop, step]
_RANGE_KWARGS = {
    "freq", "detuning", "amp", "drag", "power", "zpa_list", "time_range", "depth_range",
}


def _is_range(value: Any) -> bool:
    if isinstance(value, dict):
        return "$arange" in value
    if isinstance(value, (list, tuple)):
        return len(value) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    return False


def _range_values(value: Any) -> Sequence[float]:
    if isinstance(value, dict):
        return tuple(value["$arange"])
    return tuple(value)


def _num(value: float) -> str:
    return repr(float(value) if isinstance(value, float) or not float(value).is_integer() else int(value))


def py_literal(value: Any) -> str:
    """最小可用字面量渲染（只有本兜底路径用）。"""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return _num(value)
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(py_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{py_literal(k)}: {py_literal(v)}" for k, v in value.items()) + "}"
    return repr(value)


def normalize_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把语义参数名与"扫描区间"写法统一成 vendor 认得的形状。

    * ``freq_range`` / ``amp_range`` / ``detuning_range`` … → ``freq`` / ``amp`` / ``detuning``
    * 三元数值列表 ``[start, stop, step]`` → ``{"$arange": [...]}``
      （上游 ``py_literal`` 会渲染成 ``__import__("numpy").arange(...)``）
    """
    out: Dict[str, Any] = {}
    for raw_key, value in (params or {}).items():
        key = _KWARG_ALIASES.get(str(raw_key), str(raw_key))
        if value is None:
            continue
        if _is_range(value):
            out[key] = {"$arange": list(_range_values(value))}
        else:
            out[key] = value
    return out


def _render_params(params: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key, value in normalize_params(params).items():
        if not SAFE_ARGNAME_RE.match(str(key)):
            raise ValueError(f"invalid parameter name {key!r}")
        if isinstance(value, dict) and set(value) == {"$arange"}:
            start, stop, step = _range_values(value)
            out.append(f"{key}=np.arange({_num(start)}, {_num(stop)}, {_num(step)})")
        else:
            out.append(f"{key}={py_literal(value)}")
    return out


def _local_build(fn: str, qubits: Sequence[str], params: Dict[str, Any]) -> str:
    names = [q for q in (qubits or []) if q]
    for name in names:
        if not SAFE_QUBIT_RE.match(str(name)):
            raise ValueError(f"invalid qubit {name!r}")
    args = [repr(str(n)) for n in names] + _render_params(params)
    return f"{fn}({', '.join(args)})"


def build_vendor_call(
    fn: str,
    qubits: Sequence[str],
    params: Optional[Dict[str, Any]] = None,
    *,
    coupler: Optional[str] = None,
    report_dataset: bool = True,
) -> str:
    """拼出可执行代码。``fn`` 形如 ``sq.t1`` / ``cz.dcz``。"""
    fn = str(fn or "").strip()
    if not fn:
        raise ValueError("empty experiment function")
    names = [str(q) for q in (qubits or []) if q]
    if not names:
        raise ValueError("no qubit given")
    # 比特名一律先校验：上游 `_bit_literal` 会把非法名字降级成字符串，
    # 那样错误会拖到真机上才炸（"跑完两小时才报语法错"），所以在这里早拒。
    for name in names:
        if not SAFE_QUBIT_RE.match(name):
            raise ValueError(f"invalid qubit {name!r}: allowed [A-Za-z0-9_%-]")

    normalized = normalize_params(params)
    builder = _vendor_builder()
    if builder is not None:
        return builder(
            fn,
            names[0],
            normalized,
            report_dataset,
            qubits=names,
            coupler=coupler,
        )

    code = _local_build(fn, names, normalized)
    if report_dataset:
        probe = (
            "try:\n"
            "    print('QMCLAW_DATASET', int(_qm_last_dataset_id()))\n"
            "except Exception:\n"
            "    pass"
        )
        return f"{code}\n{probe}"
    return code

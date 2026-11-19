"""后端适配器注册表。

刻意保持极简：一个 dict + 四个函数。没有插件发现、没有 entry_points、
没有动态 import 字符串——实验室机可能是离线的，出问题时要能一眼看懂。

    from .backends import list_backends, get_adapter
    get_adapter("lqcs").capabilities()

已注册两个后端：

* ``lqcs``   —— 真机（LabRAD），实现见 ``adapters/lqcs.py``
* ``qiskit`` —— **转发**给宿主的 qiskit 服务（HTTP），实现见 ``adapters/qiskit.py``

``adapters/qiskit_stub.py`` 里的占位壳保留着（"核心不绑厂商、适配层可换后端"的物证），
但**不注册**：注册表里同名只留真实实现，``is_implemented()`` 才不会骗人。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from qwf.backend_base import (
    BackendAdapter,
    CancelToken,
    CapabilityError,
    RawResult,
    RunRequest,
)

# R2（2026-11-18）：`LqcsAdapter` 不再在模块级导入 —— 核心层（dsl/__init__.py、lint、
# manifest、executor）导入本包时会把适配器一起拉进来，形成核心→适配器的反向依赖。
# 改成按需懒导入：真正要用适配器时才 import，模块级 `__getattr__` 保留
# `from .backends import LqcsAdapter` 的兼容写法。


def _lqcs_adapter_cls():
    from .lqcs import LqcsAdapter

    return LqcsAdapter


def _qiskit_adapter_cls():
    """qiskit 适配器（懒导入）。

    它是**真实实现**：把请求转发给宿主的 qiskit 服务（HTTP）。
    `adapters/qiskit_stub.py` 里的占位壳保留着，但**不进注册表**——
    否则 `list_backends()` 里会有两个同名后端，"能不能跑"就没法一眼看懂。
    """
    from .qiskit import QiskitAdapter

    return QiskitAdapter


_REGISTRY: Dict[str, BackendAdapter] = {}


def register(adapter: BackendAdapter) -> BackendAdapter:
    if not isinstance(adapter, BackendAdapter):
        raise TypeError(f"adapter 必须继承 BackendAdapter，收到: {type(adapter).__name__}")
    _REGISTRY[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> BackendAdapter:
    key = (name or "lqcs").strip().lower()
    if key in _REGISTRY:
        return _REGISTRY[key]
    if key == "lqcs":
        return register(_lqcs_adapter_cls()())
    if key == "qiskit":
        return register(_qiskit_adapter_cls()())
    raise CapabilityError(
        f"未知后端: {name!r}；已注册: {', '.join(sorted(_REGISTRY)) or '(空)'}。"
        " 新增后端请实现 dsl.backends.base.BackendAdapter 并在此注册（见 docs/BACKEND_ADAPTERS.md）"
    )


def list_backends() -> List[str]:
    if "lqcs" not in _REGISTRY:
        register(_lqcs_adapter_cls()())
    if "qiskit" not in _REGISTRY:
        register(_qiskit_adapter_cls()())
    return sorted(_REGISTRY)


def describe(name: Optional[str] = None) -> Any:
    if name:
        return get_adapter(name).describe()
    if "lqcs" not in _REGISTRY:
        register(_lqcs_adapter_cls()())
    if "qiskit" not in _REGISTRY:
        register(_qiskit_adapter_cls()())
    return {key: adapter.describe() for key, adapter in sorted(_REGISTRY.items())}


def __getattr__(name: str) -> Any:
    """PEP 562：懒加载适配器类，兼容 `from .backends import LqcsAdapter`。"""
    if name == "LqcsAdapter":
        return _lqcs_adapter_cls()
    if name == "QiskitAdapter":
        return _qiskit_adapter_cls()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BackendAdapter", "RunRequest", "RawResult", "CancelToken", "CapabilityError",
    "LqcsAdapter", "QiskitAdapter", "register", "get_adapter", "list_backends", "describe",
]

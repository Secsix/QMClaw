"""插件桥：核心层按需加载顶层 `adapters` 包（不反向依赖、不模块级导入）。

R2（2026-11-18）在 QMClaw 仓把"核心 import 适配器"改成同包内懒导入；
R4（2026-11-18）迁到独立仓后，适配器变成**同级包** `adapters/`（不在 `qwf/` 里），
所以核心要用 importlib 按需加载它。这里是唯一的桥。
"""

from typing import Any


def _adapters():
    """载入顶层 adapters 包（首次调用时才导入）。"""
    import importlib

    return importlib.import_module("adapters")


def get_adapter(name: str):
    """取后端适配器实例（委托给 adapters 注册表）。"""
    return _adapters().get_adapter(name)


def list_backends():
    """列出已注册后端。"""
    return _adapters().list_backends()


def register(adapter: Any):
    """注册后端适配器。"""
    return _adapters().register(adapter)


def describe(name: str = None):
    """描述后端能力。"""
    return _adapters().describe(name)


def __getattr__(name: str):
    if name == "BackendAdapter":
        from .backend_base import BackendAdapter

        return BackendAdapter
    if name == "LqcsAdapter":
        return getattr(_adapters(), "LqcsAdapter")
    if name == "QiskitAdapter":
        return getattr(_adapters(), "QiskitAdapter")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

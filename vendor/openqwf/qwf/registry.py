"""Metric Registry 接入层（QMClaw Architecture Roadmap §2.4/§2.5）。

为什么需要这一层
----------------
`dsl` 包有两种加载方式：

* 正常运行时是 ``services.workflow_service.dsl``（相对导入可用）；
* 离线自检脚本会把 ``services/workflow_service`` 加进 ``sys.path`` 后
  直接 ``import dsl`` —— 这时 ``from ...common.metrics import ...`` 会越过
  顶层，报 ``attempted relative import beyond top-level package``。

所以这里统一改成**绝对导入** ``services.common.*``，并在需要时把
``qmclaw-server`` 根目录补进 ``sys.path``：两种加载方式拿到的是同一份判据，
阈值永远只有一个真源（``config/lab_criteria.json``）。

R2 分层（2026-11-18）
---------------------
原先上面三段 import 是**模块级**的，等于核心层对 QMClaw 主干（`services.common.*`）
有一条硬外部依赖 —— 独立 qwf 仓没法直接把 `dsl/` 搬走。现在改成惰性解析：

* 模块级 `__getattr__`（PEP 562）在第一次取用某个名字时才去找实现；
* 优先找**本包内**的同名模块（迁移后的实体表：`metrics.py` / `profile.py` /
  `qubit_state.py`），没有才回退 `services.common.*`（QMClaw 仓里的现状）。

两种形态都只有一个真源，核心层不再有硬依赖，也不需要在 import 时改 `sys.path`
之外的东西（`sys.path` 的补齐保留，仅为回退路径服务）。
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Any

# R4（独立仓）：这里不再推导 QMClaw 的 `services.*` 路径，也不再改 sys.path ——
# 核心层只认**本包内**的实体表（`metrics.py` / `profile.py` / `qubit_state.py`）。
# QMClaw 主干里的 `services.common.*` 只作为可选回退（独立仓里通常不存在）。

# 名字 → 本地模块名（迁移后的实体表放这里）
_LOCAL = {
    "available_levels": "metrics",
    "criteria_metric_name": "metrics",
    "evaluate": "metrics",
    "evaluate_all": "metrics",
    "load_criteria": "metrics",
    "resolve_threshold": "metrics",
    "load_profile": "profile",
    "profile_env": "profile",
    "profile_env_fields": "profile",
    "qubit_entry": "profile",
    "derive_state": "qubit_state",
    "state_table": "qubit_state",
}

# 名字 → QMClaw 主干里的实现模块
_REMOTE = {
    "metrics": "services.common.metrics",
    "profile": "services.common.profile",
    "qubit_state": "services.common.qubit_state",
}


def _local_module(name: str):
    """本包内的兄弟模块（迁移后的实体表）；没有就返回 None。"""
    try:
        return importlib.import_module(f".{name}", __package__ or "qwf")
    except Exception:  # noqa: BLE001 — 没有本地实体表属于正常情况
        return None


def _remote_module(mod_name: str):
    try:
        return importlib.import_module(_REMOTE[mod_name])
    except Exception:  # noqa: BLE001 — 独立仓里没有 QMClaw 主干，属于正常情况
        return None


def _resolve(name: str) -> Any:
    mod_name = _LOCAL[name]
    module = _local_module(mod_name)
    if module is not None and hasattr(module, name):
        return getattr(module, name)
    module = _remote_module(mod_name)
    if module is None or not hasattr(module, name):
        raise AttributeError(
            f"registry: 找不到 {name!r} 的实现（本地 {mod_name}.py 与 "
            f"{_REMOTE[mod_name]} 都没有）"
        )
    return getattr(module, name)


def __getattr__(name: str) -> Any:
    """PEP 562：模块级惰性属性（`from .registry import load_profile` 照旧可用）。"""
    if name in _LOCAL:
        return _resolve(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "available_levels",
    "criteria_metric_name",
    "derive_state",
    "evaluate",
    "evaluate_all",
    "load_criteria",
    "load_profile",
    "profile_env",
    "profile_env_fields",
    "qubit_entry",
    "resolve_threshold",
    "state_table",
]

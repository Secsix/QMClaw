"""门面：厂商语义表 —— 惰性代理到上游 `qwf/vendor_semantics.py`。

上游（独立仓 `Secsix/OpenQWF`）持有这张表的**唯一实体**：R4 迁仓时把
`services/common/replay.py` 的 `VENDOR_SEMANTICS` 物理搬了过去，并明确要求
QMClaw 侧保持**惰性代理**（不复制第二份 —— 复制就会漂移）。

本仓运行期回放（`services/common/replay.py`）里还有一份同源表，它服务于
离线仿真；两份表的一致性由 `tests/test_openqwf_drift.py` 盯着，不一致就红。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._upstream import upstream

_VENDOR = upstream("qwf.vendor_semantics")

#: 厂商语义表（`sq.<fn>` → 自动回写 / 必填参数 / 默认展开 / 典型错误）
VENDOR_SEMANTICS: Dict[str, Dict[str, Any]] = _VENDOR.VENDOR_SEMANTICS

#: 会被仿真的函数别名（写错名字也要能发现）
KNOWN_SQ_FUNCTIONS: List[str] = list(_VENDOR.KNOWN_SQ_FUNCTIONS)


def get_vendor_semantics() -> Dict[str, Dict[str, Any]]:
    """返回厂商语义表（只读语义，调用方不应修改）。"""
    return _VENDOR.get_vendor_semantics()


def known_sq_functions() -> List[str]:
    """已知的 `sq.<fn>` 名字列表。"""
    return _VENDOR.known_sq_functions()


__all__ = [
    "VENDOR_SEMANTICS",
    "KNOWN_SQ_FUNCTIONS",
    "get_vendor_semantics",
    "known_sq_functions",
]

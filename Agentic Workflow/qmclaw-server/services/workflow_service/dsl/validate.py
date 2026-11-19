"""门面：语义校验 —— 实现见上游 `vendor/openqwf/qwf/validate.py`。

与其他门面模块的**唯一差别**：这里不是 `sys.modules` 整体替换，而是显式再导出。
原因是上游把"这个实验要几个比特"收进了私有 `_qubit_arity`（词表模块不可用时
降级为 `None`），而本地 API 一直**公开** `qubit_arity`（与 `dsl.experiments`
同名同义）。这里补上这个公开名，其余一律用上游的。
"""

from __future__ import annotations

from qwf.validate import *  # noqa: F401,F403
from qwf.validate import (  # noqa: F401
    Binding,
    INTERP_RE,
    LEGACY_RE,
    REF_RE,
    Scope,
    count_writes,
    error_list,
    field_kind,
    format_diags,
    iter_statements,
    parse_ref,
    refs_in,
    unit_of,
    validate,
)
from qwf.vocabulary import qubit_arity  # noqa: F401

__all__ = [
    "validate", "format_diags", "Scope", "parse_ref", "refs_in",
    "Binding", "field_kind", "unit_of", "iter_statements", "count_writes",
    "error_list", "INTERP_RE", "REF_RE", "LEGACY_RE",
    "qubit_arity",
]

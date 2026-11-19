"""门面：`.qwf` 文本前端 —— 实现见上游 `vendor/openqwf/qwf/textdsl/`。

子模块（`lexer` / `parser` / `printer`）要**提前登记**进 `sys.modules`：
不登记的话，`import dsl.textdsl.parser` 会顺着 `qwf/textdsl/__path__` 再加载
一份 `parser.py`，模块对象就分裂了 —— `DSLSyntaxError` 会变成两个类，
`except DSLSyntaxError` 有一边接不住。
"""

from __future__ import annotations

import importlib
import sys

from .._upstream import upstream

_SUBMODULES = ("lexer", "parser", "printer")

_mod = upstream("qwf.textdsl")
for _name in _SUBMODULES:
    _sub = importlib.import_module(f"qwf.textdsl.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _sub

sys.modules[__name__] = _mod

"""门面：画布 ⇄ IR —— 实现见上游 `vendor/openqwf/qwf/canvas_ir.py`（独立仓 Secsix/OpenQWF）。

本文件**不含实现**，只把模块整体指向上游那份（`sys.modules` 替换），
于是 `dsl.canvas_ir is qwf.canvas_ir` —— 模块对象、私有名字、类型身份都只有一份。
"""

import sys

from ._upstream import upstream

sys.modules[__name__] = upstream("qwf.canvas_ir")

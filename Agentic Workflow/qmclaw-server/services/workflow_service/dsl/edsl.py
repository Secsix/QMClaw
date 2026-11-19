"""门面：Python eDSL 前端 —— 实现见上游 `vendor/openqwf/qwf/edsl.py`（独立仓 Secsix/OpenQWF）。

本文件**不含实现**，只把模块整体指向上游那份（`sys.modules` 替换），
于是 `dsl.edsl is qwf.edsl` —— 模块对象、私有名字、类型身份都只有一份。
"""

import sys

from ._upstream import upstream

sys.modules[__name__] = upstream("qwf.edsl")

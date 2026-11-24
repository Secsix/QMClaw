"""`services.workflow_service.dsl` —— 工作流 DSL 的**兼容门面**。

三个前端，一个中间表示（IR v1），一个执行器：

    文本 `.qwf` ──┐
    Python eDSL ──┼──▶ IR (dict) ──▶ validate ──▶ IrExecutor ──▶ 现有 WorkflowService
    画布 JSON ────┘

对外只需要记住四个函数：

    ir = parse_qwf(text)                    # 或 edsl.workflow(...).to_ir()
    ir = normalize(ir)                       # 补默认值 + 自动命名
    ir, diags = validate(ir)                 # 语义校验（纯函数，不连服务）
    result = run_ir(service, ir, context)    # 真正执行

实现去哪了
----------
DSL 的**实现**已经迁到独立仓 `Secsix/OpenQWF`（核心 `qwf/` + 适配器 `adapters/`）。
本仓保留一份逐字副本 `vendor/openqwf/`，本目录退化成**门面**：

* 每个模块文件只做一件事 —— 把模块指向上游那份（`sys.modules` 替换），
  所以 `dsl.const is qwf.const`：模块对象、私有名字、类型身份都只有一份，
  门面里**没有任何一份 DSL 语义实现**；
* 路径/配置由 `dsl/_upstream.py` 桥接（挂 `sys.path` + 注入
  `QWF_CONFIG_DIR` / `QWF_VOCAB_PATH` / `QWF_DATA_DIR`）。

同步与漂移检查：`tools/sync_openqwf.py`（同步）、`tools/check_openqwf_drift.py`（查漂移）。

设计文档见 `docs/DSL_DESIGN.md`，同步流程见 `docs/OPENQWF_SYNC.md`。
"""

from __future__ import annotations

from ._upstream import setup

# 必须最先：上游模块在 import 时就会算配置路径（`qwf/metrics.py` / `qwf/autorevert.py`）。
setup()

from qwf.budget import Budget, BudgetExceeded  # noqa: E402
from qwf import capabilities, quantities  # noqa: E402
from qwf.compile import CANVAS_TO_OP, build_node, ir_from_canvas, node_type_for  # noqa: E402
from qwf.canvas_ir import (  # noqa: E402
    GRAY_NODE_TYPE,
    canvas_to_ir,
    canvas_to_qwf,
    ir_diff,
    ir_equivalent,
    ir_to_canvas,
    stability_diff,
)
from qwf.dryrun import DryRunProvider  # noqa: E402
from qwf.edsl import WorkflowBuilder, workflow  # noqa: E402
from qwf.executor import (  # noqa: E402
    Cancelled,
    Halt,
    IrExecutor,
    UndefinedRef,
    run_ir,
    wrap_result,
)
from qwf.ir import (  # noqa: E402
    assign_names,
    canonical,
    ir_hash,
    iter_all_statements,
    normalize,
    validate_structure,
)
from qwf.lint import lint_ir, lint_text  # noqa: E402
from qwf.plan import format_plan, plan_checks, plan_ir  # noqa: E402
from qwf.plugins import (  # noqa: E402
    BackendAdapter,
    LqcsAdapter,
    QiskitAdapter,
    get_adapter,
    list_backends,
    register,
)
from qwf.textdsl import DSLSyntaxError, parse_file as parse_qwf_file, parse as parse_qwf  # noqa: E402
from qwf.textdsl.printer import ir_to_qwf, print_qwf, stmt_to_qwf  # noqa: E402
from qwf.validate import Scope, format_diags, parse_ref, refs_in, validate  # noqa: E402

__all__ = [
    # 校验 / 规范化
    "normalize", "assign_names", "canonical", "ir_hash",
    "validate_structure", "validate", "format_diags", "iter_all_statements",
    "plan_ir", "format_plan", "plan_checks",
    "Scope", "parse_ref", "refs_in",
    # 前端
    "parse_qwf", "parse_qwf_file", "DSLSyntaxError",
    "lint_ir", "lint_text",
    "workflow", "WorkflowBuilder",
    "ir_from_canvas", "build_node", "node_type_for", "CANVAS_TO_OP",
    # `.qwf` ⇄ 画布（IR 是中表示）
    "ir_to_qwf", "print_qwf", "stmt_to_qwf",
    "ir_to_canvas", "canvas_to_ir", "canvas_to_qwf",
    "ir_equivalent", "ir_diff", "GRAY_NODE_TYPE", "stability_diff",
    # 执行
    "IrExecutor", "run_ir", "wrap_result",
    "Halt", "Cancelled", "UndefinedRef",
    "Budget", "BudgetExceeded", "DryRunProvider",
    # 后端预留（IR v2）
    "capabilities", "quantities",
    "BackendAdapter", "LqcsAdapter", "get_adapter", "list_backends", "register",
]

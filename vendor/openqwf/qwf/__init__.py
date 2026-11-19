"""QWF —— 工作流领域专用语言（独立仓版，原 `services.workflow_service.dsl`）。

三个前端，一个中间表示（IR v1），一个执行器：

    文本 `.qwf` ──┐
    Python eDSL ──┼──▶ IR (dict) ──▶ validate ──▶ IrExecutor ──▶ 现有 WorkflowService
    画布 JSON ────┘

对外只需要记住四个函数：

    ir = parse_qwf(text)                    # 或 edsl.workflow(...).to_ir()
    ir = normalize(ir)                       # 补默认值 + 自动命名
    ir, diags = validate(ir)                 # 语义校验（纯函数，不连服务）
    result = run_ir(service, ir, context)    # 真正执行

设计文档见 `docs/DSL_DESIGN.md`。
"""

from .budget import Budget, BudgetExceeded
from . import capabilities, paths, quantities, vocabulary
from .plugins import BackendAdapter, get_adapter, list_backends, register
from .compile import CANVAS_TO_OP, build_node, ir_from_canvas, node_type_for
from .canvas_ir import (
    GRAY_NODE_TYPE,
    canvas_to_ir,
    canvas_to_qwf,
    ir_diff,
    ir_equivalent,
    ir_to_canvas,
    stability_diff,
)
from .dryrun import DryRunProvider
from .edsl import WorkflowBuilder, workflow
from .executor import Cancelled, Halt, IrExecutor, UndefinedRef, run_ir, wrap_result
from .ir import assign_names, canonical, ir_hash, iter_all_statements, normalize, validate_structure
from .lint import lint_ir, lint_text
from .migrate import MIGRATIONS, migrate
from .plan import format_plan, plan_checks, plan_ir
from .resume import CrashInjected, ResumeError, build_checkpoint, load_checkpoint, restore
from .textdsl import DSLSyntaxError, parse_file as parse_qwf_file, parse as parse_qwf
from .textdsl.printer import ir_to_qwf, print_qwf, stmt_to_qwf
from .validate import Scope, format_diags, parse_ref, refs_in, validate

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
    # 配置路径与实验词表（可注入）
    "paths", "vocabulary",
    "migrate", "MIGRATIONS",
    # F3：崩溃恢复
    "build_checkpoint", "load_checkpoint", "restore", "CrashInjected", "ResumeError",
    "BackendAdapter", "LqcsAdapter", "QiskitAdapter", "get_adapter", "list_backends", "register",
]


def __getattr__(name: str):
    """PEP 562：懒加载适配器类。

    R2/R4（2026-11-18）：`import qwf` 不再连带把适配器（`adapters.lqcs`）拉进来 ——
    核心层不该反向依赖适配器。需要时按需取：`dsl.LqcsAdapter` / `dsl.get_adapter("lqcs")`。
    """
    if name == "LqcsAdapter":
        from .plugins import LqcsAdapter

        return LqcsAdapter
    if name == "QiskitAdapter":
        from .plugins import QiskitAdapter

        return QiskitAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

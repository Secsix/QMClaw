"""实验语义层的契约：请求 / 结果 / 后端协议。

设计意图（2026-11-26 收口）：

* 技能、智能体、前端只通过 ``POST /experiments/run`` 表达"跑哪个实验、给什么参数"；
* "这个实验对应哪个后端函数、参数怎么拼成代码"只存在于测控微服务内部；
* 每个后端（``lqcs`` / ``qiskit`` / ``offline_sim``）实现同一份 ``Backend`` 协议，
  于是换后端不影响任何调用方。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Protocol, Tuple


class BackendError(Exception):
    """某个后端明确"不归我管 / 不可用"（区别于"跑了但结果失败"）。

    ``reason`` 会原样进入 ``/experiments/run`` 的错误明细，让看板能显示
    "为什么这个实验在这台机器上跑不了"，而不是一句笼统的 500。
    """

    def __init__(self, backend: str, reason: str):
        super().__init__(f"{backend}: {reason}")
        self.backend = backend
        self.reason = reason


@dataclass(frozen=True)
class ExperimentRequest:
    """一次实验请求（后端无关）。"""

    kind: str
    qubits: Tuple[str, ...] = ()
    params: Dict[str, Any] = field(default_factory=dict)
    shots: Optional[int] = None
    seed: Optional[int] = None
    timeout: Optional[float] = None
    do_plot: bool = True
    coupler: Optional[str] = None
    report_dataset: bool = True
    task_id: Optional[str] = None

    def replace(self, **changes: Any) -> "ExperimentRequest":
        return replace(self, **changes)


@dataclass
class ExperimentResult:
    """一次实验结果（后端无关的归一形状）。"""

    ok: bool
    kind: str
    backend: str
    status: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    plot_path: Optional[str] = None
    dataset_ids: List[Any] = field(default_factory=list)
    run_id: Optional[str] = None
    elapsed_ms: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    fn: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转成 HTTP 响应（``raw`` 默认不外发，带 ``?debug=1`` 时由 handler 追加）。"""
        return {
            "ok": self.ok,
            "kind": self.kind,
            "fn": self.fn,
            "backend": self.backend,
            "status": self.status,
            "metrics": dict(self.metrics or {}),
            "plotPath": self.plot_path,
            "datasetIds": list(self.dataset_ids or []),
            "run_id": self.run_id,
            "elapsed_ms": self.elapsed_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "warnings": list(self.warnings or []),
        }


class Backend(Protocol):
    """后端协议：三个实现（lqcs / qiskit / offline_sim）语义完全一致。"""

    name: str

    def available(self) -> bool:
        """当前进程这一刻能不能用（探测结果可能被缓存）。"""
        ...

    def supports(self, kind: str) -> bool:
        """这个后端会不会跑这种实验（静态能力，不看数据）。"""
        ...

    def run(self, request: ExperimentRequest) -> ExperimentResult:
        """跑一次。不支持/不可用时抛 ``BackendError``，跑失败则返回 ``ok=False``。"""
        ...

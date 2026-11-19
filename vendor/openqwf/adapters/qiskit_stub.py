"""Qiskit 后端契约占位（F2 的第二后端，本仓只给壳）。

F2「第二后端」在 V2_PLAN 里只要求**证明分层是真的**：核心层不认厂商，
换一个后端只写一个文件。所以这里不实现任何真机路径，而是把
`BackendAdapter` 的每个口子都摆出来、标明"该返回什么形状"，
并让 `is_implemented() -> False`，任何真正下发都直接抛 NotImplementedError。

意图（写清楚才好被替换）：

    后端名      : "qiskit"
    落地方式    : qiskit-aer 本地模拟器（离线、CI 可跑），非真机
    与 lqcs 区别: 没有 HTTP、没有数据集目录、没有寄存器；run 返回内存里的
                  `counts`/`probabilities`，`dataset` 是进程内 handle

替换步骤：
    1. 依赖 qiskit 时把 `_require_qiskit()` 换成真实 import；
    2. 实现 `run()`：把 `RunRequest.code`（`sq.*` 语句）翻译成 circuit，
       或让调用方传 `ir`（推荐——v2 的 `run{experiment}` 正是为此准备）；
    3. 实现 `read_param/write_param`（模拟器只有进程内 dict）；
    4. 在 `adapters/__init__.py` 的 `get_adapter()` 里加一个 `elif key == "qiskit"`。

为什么留在仓库里：只要这个文件存在且能 `register()`，`dsl_ci.py` 的
S 组就能断言"核心层没有硬编码 lqcs"，F2 的分层主张才有物证。

2026-11-19（A3 组）：真实实现已落地在 **`adapters/qiskit.py`** ——
它是"**转发**到宿主 qiskit 服务（HTTP）"的适配器，而不是本仓进程内跑 Aer
（电路构造的真源在宿主 `services/quantum_service/`，避免两套实现漂移）。
本文件因此**继续保留但不注册**：注册表里 `qiskit` 只留真实实现，
否则 `list_backends()` 里会有两个同名后端、`is_implemented()` 会骗人。
`register_stub()` 只在"想看看契约占位长什么样"时手工调用。

注意：本文件**不 import 任何真实 qiskit**（`_require_qiskit()` 只是一个恒抛的
占位函数），所以 `import adapters` 不会因为缺 qiskit 而炸。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from qwf.backend_base import (
    BackendAdapter,
    CancelToken,
    CapabilityError,
    RawResult,
    RunRequest,
)

#: 该后端认识的实验名（对应 `sq.*` 的语义名，不含厂商前缀）
_SUPPORTED = (
    "iqraw",
    "spectroscopy",
    "ramsey",
    "t1",
    "power_rabi",
    "zpa_scan",
)


def _require_qiskit() -> None:
    """F2 落地时才调用；现在恒抛，避免"看起来能跑"。"""
    raise NotImplementedError(
        "qiskit 后端尚未实现（V2_PLAN F2 不在本次范围）；"
        "先用 adapters.lqcs（本机量子服务 HTTP）"
    )


class QiskitStubAdapter(BackendAdapter):
    """契约占位：所有方法都返回合法形状或显式 NotImplementedError。"""

    name = "qiskit"
    label = "Qiskit Aer（未实现，契约占位）"

    # ── 能力与健康 ──────────────────────────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "implemented": False,
            "kind": "simulator",
            "experiments": list(_SUPPORTED),
            "features": {
                "dataset": True,      # 进程内 handle
                "registers": False,   # 模拟器没有寄存器快照
                "fit": False,         # 拟合仍在 analysis 服务侧
                "cancel": False,
            },
            "notes": "F2 范围外；接口形状与 lqcs 对齐，便于替换",
        }

    def health(self) -> Dict[str, Any]:
        return {"ok": False, "error": "qiskit adapter not implemented", "backend": self.name}

    def devices(self) -> List[Dict[str, Any]]:
        return [{"id": "aer_simulator", "label": "Aer（未实现）", "online": False}]

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "implemented": False,
            "capabilities": self.capabilities(),
        }

    # ── 执行 ────────────────────────────────────────────────────────────
    def run(self, req: RunRequest, token: Optional[CancelToken] = None) -> RawResult:
        _require_qiskit()
        raise AssertionError("unreachable")

    def fit(self, kind: str, data_ref: Any) -> Dict[str, Dict[str, Any]]:
        _require_qiskit()
        raise AssertionError("unreachable")

    # ── 参数（模拟器只有进程内 dict）────────────────────────────────────
    def read_param(self, path: str) -> Dict[str, Any]:
        _require_qiskit()
        raise AssertionError("unreachable")

    def write_param(self, path: str, quantity: Dict[str, Any]) -> None:
        _require_qiskit()

    # ── 静态语义（核心层预算/漂移要用；不依赖运行期）───────────────────
    def write_effects(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        # 模拟器不改真机参数 ⇒ 没有写效应。这是与 lqcs 的**语义差别**，
        # 正好用来验证核心层没有把"写效应"当成厂商常量。
        return {"writes": [], "reason": "simulator"}

    def estimate_runtime(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        shots = int((params or {}).get("shots") or 1024)
        return {"seconds": 0.5 + shots / 20000.0, "source": "qiskit_stub"}

    def write_key(self, name: str, path: Optional[List[str]] = None) -> Optional[str]:
        return None

    def drift_threshold(self, key: str) -> Optional[Dict[str, Any]]:
        return None

    def is_implemented(self) -> bool:
        return False


def register_stub() -> QiskitStubAdapter:
    """把占位后端登记进注册表（`dsl_ci.py` 与文档示例用）。

    注意：**不要在 `adapters/__init__.py` 里自动调用**——登记的适配器会被
    `list_backends()` 列出来，那会让"未知后端"的报错信息失去意义。

    2026-11-19 起还有另一条理由：真实实现（`adapters/qiskit.py`）也用
    `name="qiskit"`，手工调本函数会**覆盖**真实实现。要看真实实现用
    `adapters.get_adapter("qiskit")`。
    """
    from . import register

    return register(QiskitStubAdapter())


__all__ = ["QiskitStubAdapter", "register_stub"]

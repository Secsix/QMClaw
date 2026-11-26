# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""语义工具：让智能体只说「跑哪个实验、拟合哪个数据集」。

背景（2026-11-26，服务边界收口）：以前暴露给智能体的是 `run_code(code)` ——
它把一段调用厂商库的 Python 代码直接送进测控服务执行。这条路绕过了三样东西：

1. **后端适配**：同一段代码只对 lqcs 成立，qiskit / 离线回放根本看不懂；
2. **参数校验**：代码里的参数名写错要到真机上才炸；
3. **可审计**：代码字符串无法描述「跑的是哪个实验、哪个 kind」。

所以工具面收敛成两个语义工具：

- `run_experiment(kind, qubit, params)` → `POST /experiments/run`
- `analyze_dataset(dataset_id, kind)` → `POST /fit`

`run_code` 仍然保留（排障用），但**默认不注册**，要显式打开
（`QMCLAW_EXPOSE_RUN_CODE=1`）。默认状态下智能体看不到它。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .base import ParameterSchema, QuantumTool, ToolResult, ToolSchema
from .errors import ErrorCode

# 语义工具的默认超时：实验可能要跑几分钟，拟合一般很快
RUN_TIMEOUT = 900.0
FIT_TIMEOUT = 300.0


def expose_run_code() -> bool:
    """是否把 `run_code` 暴露给智能体（默认关闭）。

    只在排障时由人临时打开；打开后仍然是**同一条服务链路**（写闸门、审计照旧），
    只是允许直接下发代码。
    """
    raw = str(os.environ.get("QMCLAW_EXPOSE_RUN_CODE", "")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _post_json(url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """POST JSON 并返回解析后的 body；HTTP 错误也把 body 带回来（服务端有结构化错误）。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 网络层异常统一报给调用方
        return {"error": f"请求 {url} 失败：{exc}"}
    try:
        parsed = json.loads(body)
    except Exception:  # noqa: BLE001
        return {"error": f"{url} 返回的不是 JSON：{body[:300]}"}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


class SemanticRunExperimentTool(QuantumTool):
    """按 kind 跑一次实验（语义入口，三后端通用）。

    与旧的 `run_code` 的区别：这里说「iqraw / t1 / spectroscopy」，
    由测控服务决定该实验在哪个后端可用、该拼什么调用、参数名对不对。
    """

    def __init__(self, quantum_service_url: str = "http://localhost:3003"):
        self._url = quantum_service_url.rstrip("/")

    @property
    def name(self) -> str:
        return "run_experiment"

    @property
    def description(self) -> str:
        return (
            "Run one experiment by kind (e.g. iqraw, t1, ramsey_df, spectroscopy, xeb) on a qubit. "
            "The measurement service picks the backend (lqcs / qiskit / offline_sim) and validates "
            "parameter names. Returns metrics, plot path, run_id and dataset ids. "
            "Use GET /api/quantum/experiments/catalog to see which kinds are supported."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "kind": ParameterSchema(
                    name="kind",
                    type="string",
                    description="Experiment kind, e.g. 'iqraw', 't1', 'ramsey_df', 'spectroscopy', 'xeb'.",
                    required=True,
                ),
                "qubit": ParameterSchema(
                    name="qubit",
                    type="string",
                    description="Target qubit name (e.g. 'q0').",
                    required=True,
                ),
                "params": ParameterSchema(
                    name="params",
                    type="object",
                    description="Experiment parameters (e.g. {'zpa': 0.0, 'freq': [4.0, 3.9, -0.01]}).",
                    required=False,
                ),
                "backend": ParameterSchema(
                    name="backend",
                    type="string",
                    description="Force a backend: 'auto' (default), 'lqcs', 'qiskit', 'offline_sim'.",
                    required=False,
                    enum=["auto", "lqcs", "qiskit", "offline_sim"],
                ),
            },
        )

    def execute(
        self,
        kind: str,
        qubit: str,
        params: Optional[Dict[str, Any]] = None,
        backend: str = "auto",
        **kwargs: Any,
    ) -> ToolResult[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "kind": str(kind or "").strip(),
            "qubits": [str(qubit or "").strip()],
            "params": dict(params or {}),
        }
        forced = str(backend or "auto").strip().lower()
        if forced and forced != "auto":
            payload["backend"] = forced

        if not payload["kind"]:
            return ToolResult.err("kind is required", error_code=ErrorCode.VALIDATION_ERROR.value)
        if not payload["qubits"][0]:
            return ToolResult.err("qubit is required", error_code=ErrorCode.VALIDATION_ERROR.value)

        result = _post_json(f"{self._url}/experiments/run", payload, RUN_TIMEOUT)
        if result.get("error") or result.get("success") is False:
            return ToolResult.err(
                str(result.get("error") or "experiment failed"),
                error_code=str(result.get("error_code") or ErrorCode.INTERNAL_ERROR.value),
                hint=result.get("hint"),
                attempts=result.get("attempts"),
                backend=result.get("backend"),
            )
        return ToolResult.ok(
            result,
            backend=result.get("backend"),
            run_id=result.get("run_id"),
            dataset_ids=result.get("dataset_ids"),
        )


class AnalyzeDatasetTool(QuantumTool):
    """按 kind 拟合一个数据集（语义入口，拟合口径在服务里）。"""

    def __init__(self, analysis_service_url: str = "http://localhost:3004"):
        self._url = analysis_service_url.rstrip("/")

    @property
    def name(self) -> str:
        return "analyze_dataset"

    @property
    def description(self) -> str:
        return (
            "Fit one dataset by kind (t1, ramsey_df, xeb, iqraw, spectroscopy, piamp, drag_calibrate ...) "
            "and return physical quantities plus state-machine evidence. "
            "The analysis service owns the fitting models and unit conversion. "
            "Set do_plot=true to also get a plot URL."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "dataset_id": ParameterSchema(
                    name="dataset_id",
                    type="string",
                    description="Dataset id to fit (offline index like '20251103_00485' or a run's dataset id).",
                    required=True,
                ),
                "kind": ParameterSchema(
                    name="kind",
                    type="string",
                    description="Fit kind, e.g. 't1', 'ramsey_df', 'xeb', 'iqraw'. Omit to infer from the dataset.",
                    required=False,
                ),
                "do_plot": ParameterSchema(
                    name="do_plot",
                    type="boolean",
                    description="Also render a fit figure and return its plot_url.",
                    required=False,
                    default=False,
                ),
                "engine": ParameterSchema(
                    name="engine",
                    type="string",
                    description="Fitting engine: 'scipy' (default), 'lqcs', 'auto'.",
                    required=False,
                    enum=["scipy", "lqcs", "auto"],
                ),
            },
        )

    def execute(
        self,
        dataset_id: str,
        kind: Optional[str] = None,
        do_plot: bool = False,
        engine: str = "scipy",
        **kwargs: Any,
    ) -> ToolResult[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "dataset_id": str(dataset_id or "").strip(),
            "do_plot": bool(do_plot),
            "engine": str(engine or "scipy").strip().lower(),
        }
        if not payload["dataset_id"]:
            return ToolResult.err("dataset_id is required", error_code=ErrorCode.VALIDATION_ERROR.value)
        if kind:
            payload["kind"] = str(kind).strip()

        result = _post_json(f"{self._url}/fit", payload, FIT_TIMEOUT)
        if result.get("ok") is False or result.get("error"):
            return ToolResult.err(
                str(result.get("reason") or result.get("error") or "fit failed"),
                error_code=str(result.get("error_code") or "fit_failed"),
                kind=result.get("kind"),
                kinds=result.get("kinds"),
            )
        return ToolResult.ok(
            result,
            kind=result.get("kind"),
            r2=result.get("r2"),
            plot_url=result.get("plot_url"),
        )


__all__ = [
    "AnalyzeDatasetTool",
    "SemanticRunExperimentTool",
    "expose_run_code",
    "FIT_TIMEOUT",
    "RUN_TIMEOUT",
]

"""三个后端的统一适配：``lqcs`` / ``qiskit`` / ``offline_sim``。

每个适配器都很薄 —— 它只做三件事：

1. 说清自己**能不能**跑这种 kind（静态能力，来自 ``catalog``）；
2. 把 ``ExperimentRequest`` 拼成该后端要的入参（代码 / payload）；
3. 把后端返回归一成 ``ExperimentResult``。

真正的硬件交互仍在 service 里（``_handle_execute`` / ``_execute_with_qiskit`` /
``_execute_offline_simulation``），所以写闸门、任务账本、审计一个都不绕过。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import catalog
from .base import BackendError, ExperimentRequest, ExperimentResult
from .codegen import build_vendor_call

BACKEND_LQCS = "lqcs"
BACKEND_QISKIT = "qiskit"
BACKEND_OFFLINE = "offline_sim"


def _dataset_ids(payload: Dict[str, Any]) -> List[Any]:
    ids: List[Any] = []
    for key in ("dataset_ids", "datasetIds", "dataset_id"):
        value = payload.get(key)
        if isinstance(value, list):
            ids.extend(value)
        elif value not in (None, "", 0):
            ids.append(value)
    extra = payload.get("extra")
    if isinstance(extra, dict):
        for key in ("dataset_ids", "datasetIds", "dataset_id"):
            value = extra.get(key)
            if isinstance(value, list):
                ids.extend(value)
            elif value not in (None, "", 0):
                ids.append(value)
    # 去重保序
    seen, out = set(), []
    for item in ids:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _result_from_payload(
    payload: Dict[str, Any], *, kind: str, backend: str, fn: str
) -> ExperimentResult:
    status = str(payload.get("status") or "")
    error = payload.get("error")
    if isinstance(error, str) and error.strip().lower() in ("", "none", "null"):
        error = None
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        warnings = [str(w) for w in warnings]
    else:
        warnings = []
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    return ExperimentResult(
        ok=bool(payload.get("ok", status in ("completed", "qiskit_completed", "success", "lqcs_completed")) and not error),
        kind=kind,
        backend=str(payload.get("backend") or backend),
        status=status,
        metrics=metrics,
        plot_path=payload.get("plotPath") or payload.get("plot_path"),
        dataset_ids=_dataset_ids(payload),
        run_id=payload.get("run_id"),
        elapsed_ms=payload.get("elapsed_ms"),
        stdout=str(payload.get("stdout") or ""),
        stderr=str(payload.get("stderr") or ""),
        error=str(error) if error else None,
        warnings=warnings,
        fn=fn,
        raw=payload,
    )


class _ServiceBackend:
    """所有适配器的共同部分：持有 service + kind 能力查询。"""

    name = ""

    def __init__(self, service: Any):
        self.service = service

    def supports(self, kind: str) -> bool:
        return catalog.supports(kind, self.name)

    def _spec(self, kind: str) -> Dict[str, Any]:
        spec = catalog.get_spec(kind)
        if not spec:
            raise BackendError(self.name, f"未知实验 kind {kind!r}（看 GET /experiments/catalog）")
        return spec

    def _execute(
        self,
        code: str,
        request: ExperimentRequest,
        force: Optional[str] = None,
    ) -> Dict[str, Any]:
        """统一走 ``_handle_execute``：审计记录 / run_id / warnings 一并保留。

        语义层的"强制后端"通过内部键 ``_force_backend`` 下传（只有本模块会带）。
        """
        body: Dict[str, Any] = {
            "code": code,
            "qubits": list(request.qubits),
            "qubit": request.qubits[0] if request.qubits else "",
            "async": False,
            "wait": True,
            "shots": request.shots,
            "seed": request.seed,
            "timeout": request.timeout,
            "task_id": request.task_id,
        }
        if force:
            body["_force_backend"] = force
        return self.service._handle_execute(body)

    def _build_code(self, request: ExperimentRequest, spec: Dict[str, Any]) -> str:
        params = dict(request.params or {})
        if request.shots is not None:
            params.setdefault("shots", request.shots)
        if request.seed is not None:
            params.setdefault("seed", request.seed)
        # 服务端执行一律不在工作线程里画图：do_plot=True 会拉起 GUI 把微服务卡死，
        # 需要图时后端自己的 _generate_simulation_plot 会出（qiskit 路径）。
        params.setdefault("do_plot", False)
        try:
            return build_vendor_call(
                spec["fn"],
                list(request.qubits),
                params,
                coupler=request.coupler,
                report_dataset=request.report_dataset,
            )
        except ValueError as exc:
            raise BackendError(self.name, str(exc)) from exc


class LqcsBackend(_ServiceBackend):
    """真机（LabRAD / lqms）。"""

    name = BACKEND_LQCS

    def available(self) -> bool:
        try:
            state = self.service._policy.state(BACKEND_LQCS)
            return bool(getattr(state, "available", False))
        except Exception:
            return False

    def run(self, request: ExperimentRequest) -> ExperimentResult:
        spec = self._spec(request.kind)
        code = self._build_code(request, spec)
        active = None
        try:
            active = self.service._policy.active(self.service._get_mode())
        except Exception:
            active = None
        if active != BACKEND_LQCS:
            raise BackendError(
                self.name,
                f"策略当前生效后端是 {active!r}（真机未连接）；先用 POST /mode {{'mode':'online'}} 或改 backends.order",
            )
        payload = self._execute(code, request)
        result = _result_from_payload(payload, kind=request.kind, backend=BACKEND_LQCS, fn=spec["fn"])
        if result.backend != BACKEND_LQCS:
            raise BackendError(
                self.name,
                f"请求被 {result.backend!r} 接走了（策略未把真机列为生效后端）",
            )
        return result


class QiskitBackend(_ServiceBackend):
    """qiskit/Aer 模拟（没有真机的机器上的主路径）。"""

    name = BACKEND_QISKIT

    def available(self) -> bool:
        try:
            from services.quantum_service import qiskit_backend
            info = qiskit_backend.status() or {}
            return bool(info.get("available")) and bool(info.get("enabled", True))
        except Exception:
            return False

    def run(self, request: ExperimentRequest) -> ExperimentResult:
        spec = self._spec(request.kind)
        code = self._build_code(request, spec)
        payload = self._execute(code, request, force=BACKEND_QISKIT)
        if payload.get("backend") not in (BACKEND_QISKIT, None) and not payload.get("ok"):
            raise BackendError(
                self.name,
                f"qiskit 词表里没有 {request.kind!r}（当前支持：{', '.join(catalog.supported_kinds(BACKEND_QISKIT))}）",
            )
        return _result_from_payload(payload, kind=request.kind, backend=BACKEND_QISKIT, fn=spec["fn"])


class OfflineBackend(_ServiceBackend):
    """历史数据回放 / 模拟器（没真机、没 qiskit 时的降级路径）。"""

    name = BACKEND_OFFLINE

    def available(self) -> bool:
        return True

    def run(self, request: ExperimentRequest) -> ExperimentResult:
        spec = self._spec(request.kind)
        code = self._build_code(request, spec)
        payload = self._execute(code, request, force=BACKEND_OFFLINE)
        return _result_from_payload(payload, kind=request.kind, backend=BACKEND_OFFLINE, fn=spec["fn"])


ADAPTERS = {
    BACKEND_LQCS: LqcsBackend,
    BACKEND_QISKIT: QiskitBackend,
    BACKEND_OFFLINE: OfflineBackend,
}


def adapter_for(service: Any, name: str):
    cls = ADAPTERS.get(name)
    if cls is None:
        raise BackendError(name, f"未知后端 {name!r}（可用：{', '.join(ADAPTERS)}）")
    return cls(service)


def backend_status(service: Any) -> List[Dict[str, Any]]:
    """``GET /experiments/catalog`` 想显示"这台机器现在能用哪个后端"时用。"""
    out: List[Dict[str, Any]] = []
    for name in ADAPTERS:
        adapter = adapter_for(service, name)
        try:
            available = bool(adapter.available())
        except Exception as exc:  # noqa: BLE001 —— 探测失败只报不可用
            available = False
            out.append({"backend": name, "available": False, "error": f"{type(exc).__name__}: {exc}",
                        "kinds": len(catalog.supported_kinds(name))})
            continue
        out.append({"backend": name, "available": available, "kinds": len(catalog.supported_kinds(name))})
    return out


def _order(service: Any, backend: Optional[str]) -> List[str]:
    if backend:
        return [backend]
    try:
        order = list(service._policy.resolve(service._get_mode()) or [])
    except Exception:
        order = []
    if not order:
        order = [BACKEND_LQCS, BACKEND_QISKIT, BACKEND_OFFLINE]
    return [name for name in order if name in ADAPTERS]


def run_experiment(
    service: Any,
    request: ExperimentRequest,
    backend: Optional[str] = None,
    *,
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    """跑一次实验，返回 ``{"result": ExperimentResult, "attempts": [...]}``。

    * ``backend`` 显式给出 → 只试它（失败即报，方便排查"到底是谁在跑"）；
    * 没给 → 按 ``BackendPolicy`` 的顺序依次尝试，第一个成功即返回。

    每个失败尝试都留一条 ``{backend, reason}``，让调用方知道"为什么没走它"。
    """
    spec = catalog.get_spec(request.kind)
    if not spec:
        raise BackendError("catalog", f"未知实验 kind {request.kind!r}（看 GET /experiments/catalog）")

    attempts: List[Dict[str, Any]] = []
    order = _order(service, backend)
    if not order:
        raise BackendError("policy", "没有任何可用后端（检查 config/fallback_config.json 的 backends.order）")

    last_error: Optional[str] = None
    for name in order:
        adapter = adapter_for(service, name)
        if not adapter.supports(request.kind):
            attempts.append({"backend": name, "skipped": "kind_not_supported"})
            last_error = f"{name}: 不支持 {request.kind!r}"
            continue
        try:
            if not adapter.available():
                attempts.append({"backend": name, "skipped": "unavailable"})
                last_error = f"{name}: 不可用"
                continue
        except Exception as exc:  # noqa: BLE001
            attempts.append({"backend": name, "skipped": "probe_failed", "error": str(exc)})
            last_error = f"{name}: 探测失败 {exc}"
            continue
        try:
            result = adapter.run(request)
        except BackendError as exc:
            attempts.append({"backend": name, "skipped": "backend_error", "error": exc.reason})
            last_error = str(exc)
            continue
        except Exception as exc:  # noqa: BLE001 —— 后端异常不外冒
            attempts.append({"backend": name, "skipped": "exception", "error": f"{type(exc).__name__}: {exc}"})
            last_error = f"{name}: {type(exc).__name__}: {exc}"
            continue
        attempts.append({"backend": name, "ok": result.ok, "status": result.status})
        if result.ok:
            return {"result": result, "attempts": attempts}
        last_error = f"{name}: {result.error or result.status or 'failed'}"
        if not allow_fallback or backend:
            return {"result": result, "attempts": attempts}

    detail = "；".join(
        f"{a['backend']}={a.get('skipped') or a.get('status') or a.get('error')}" for a in attempts
    )
    raise BackendError(
        "catalog",
        f"{request.kind}: " + (detail or str(last_error or "没有可用后端")),
    )

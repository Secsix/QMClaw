"""qiskit 适配器：**转发**给宿主的 qiskit 服务（HTTP），不在本仓做电路构造。

分工（红线）
------------
本仓（OpenQWF / QWF）只负责 IR、校验、执行器与**厂商中立的契约**；
真正的 qiskit/Aer 线路构造与模拟实现住在**宿主**（QMClaw 的
`services/quantum_service/qiskit_backend.py` + `qiskit_experiments.py` + `qiskit_circuits.py`）。

本文件**只做 HTTP 转发**，理由有三条：

1. **单一真源**：如果上游再实现一份进程内 Aer，就会出现"两套电路构造"——
   同一个实验在两处各造一次线路，参数名、噪声模型、指标口径会悄悄漂移。
   转发让"实验到底怎么跑的"只有一处答案（宿主的 `qiskit_experiments.EXPERIMENT_REGISTRY`）。
2. **依赖边界**：`pyproject.toml` 的 `dependencies` 为空是硬约束（`qwf/` 与 `adapters/`
   禁止第三方依赖）。qiskit 是可选依赖，装在宿主的服务环境里，不装在本仓。
3. **分层不变量**：`qwf/` 核心层不 import `adapters`（PEP 562 惰性 + `qwf/plugins.py` 桥），
   本文件同样不 import 任何厂商库——只用标准库 `urllib.request`。

请求 / 响应契约（宿主 `services/quantum_service/server.py`）
----------------------------------------------------------
三个端点（都在 quantum_service，默认端口 3003）：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/qiskit/run` | 跑一次实验（或一段 QASM2 自定义线路） |
| GET  | `/qiskit/health` | 可用性 / 版本 / 执行方式（永远 200，不抛） |
| GET  | `/qiskit/experiments` | 实验目录（13 个实验的声明；不需要装 qiskit 也能调） |

`POST /qiskit/run` 请求体字段（宿主 `qiskit_backend._build_request` 的入参口径）：

| 字段 | 类型 | 说明 | 本适配器的来源 |
|---|---|---|---|
| `experiment` | str | 实验名，取自宿主实验词表（`t1` / `ramsey` / `swap` / `iqraw` …） | `RunRequest.experiment`（去掉 `sq.` 前缀，再过别名表） |
| `qubit` | str | 单个比特名 | `RunRequest.qubit` |
| `qubits` | list[str] | 多比特（顺序 = 线路里的物理比特顺序） | `RunRequest.qubits`（**上游尚未加**，见下）或 `options["qubits"]` |
| `params` | dict | 实验参数（宿主按 spec 过滤，非法键会被丢弃） | `RunRequest.params`（`{"value": x}` 形式会被拆成裸值） |
| `shots` | int | 采样数（宿主侧会钳位到 `max_shots`） | `RunRequest.shots` |
| `seed` | int | 随机种子 | `RunRequest.seed`（**上游尚未加**）或 `options["seed"]` |
| `circuit` | str | 可选，QASM2 源码（绕过实验目录直接跑） | `options["circuit"]` |

响应（宿主统一信封）关键字段：`status`（`qiskit_completed` / `qiskit_error` /
`qiskit_unavailable` / `qiskit_timeout`）、`task_id`、`experiment`、`qubit(s)`、
`metrics`、`data`（绘图数据）、`plotPath`、`stdout`、`error`、`elapsed_ms`、
`unit_system`（固定 `qiskit_normalized`）、`approximation`（`exact` / `model`）。

**不写设备参数**
----------------
qiskit 是纯本地模拟：没有 LabRAD 寄存器、没有 `f10/f21`、没有 `PiGate.amp`。
所以 `write_effects()` 恒为 `mode="none"`、`effective=False`——模拟器跑完，
真机的频率/幅度/判据一个字节都没动。这一点必须显式声明，否则执行器的
"写闸门预览"会把模拟结果当成真机写副作用。

**拟合不在这里**
----------------
拟合在 analysis 侧（宿主 `qter.fitData` / analysis_service）。本适配器的 `fit()`
显式抛 `NotImplementedError`，与"能力靠声明、不靠试错"一致。

**单位制提醒**
--------------
宿主 qiskit 的结果带 `unit_system="qiskit_normalized"`：量纲按规范单位标注，
但值来自模拟（含先验参数），**不得当作真机标定值**。本适配器把它原样透传到
`RawResult.meta["unit_system"]`。

因为单位是**约定**而不是事实，适配器默认**不**把宿主指标换算成规范数量
（`RawResult.quantities` 留空），原始指标一律放在 `artifacts["metrics"]`。
需要对照数量时显式开 `QiskitAdapter(map_quantities=True)`，换算约定写在
`QISKIT_HOST_UNITS`（时间：秒 → us；频率：Hz → GHz；比量：原值）。

**上游待补的字段（A1 组，多比特 IR）**
------------------------------------
`qwf/backend_base.py` 的 `RunRequest` 目前只有单数的 `qubit: str`，没有
`qubits: list[str]` 与 `seed`。本文件用 `getattr(req, "qubits", None)` 兼容读取，
因此 A1 组一旦补上字段，这里**无需改动**即自动生效；在补上之前，
多比特只能通过 `options["qubits"]` 传（`RunRequest.options` 是"适配器私有开关"）。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from qwf import quantities
from qwf.backend_base import BackendAdapter, CancelToken, RawResult, RunRequest

#: 宿主 quantum_service 的默认地址（与 `qwf/host.py` 的默认值一致）。
DEFAULT_BASE_URL = "http://127.0.0.1:3003"

#: 环境变量名：与 `qwf/host.py` 里 `_quantum_service_url` 用的是同一个。
ENV_BASE_URL = "QWF_QUANTUM_URL"

#: IR/真机实验名 → 宿主 qiskit 实验目录名。
#: 与宿主 `QuantumService.QISKIT_EXPERIMENT_ALIASES` 保持一致（那是真源，
#: 这里只是转发前的本地映射，宿主侧还会再过一次自己的别名表）。
EXPERIMENT_ALIASES = {
    "ramsey_df": "ramsey",
    "spectroscopy_iq": "pulsed_spec",
}

#: 每个实验要用几个比特（1 = 单比特；2 = 两比特线路）。
#: 宿主实验目录里没有 arity 字段，所以这张表随本适配器维护；
#: 依据是宿主 `qiskit_circuits.py` 里各 builder 的构造方式
#: （`swap` 是两比特交换 + Bell 基测量，`drag_calibrate` 用第二个比特做泄漏旗标）。
EXPERIMENT_ARITY = {
    "swap": 2,
    "drag_calibrate": 2,
}

#: 宿主返回的"成功"状态值。
STATUS_COMPLETED = "qiskit_completed"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


#: 宿主 qiskit 归一化单位下，各量纲所用的单位（**约定，不是保证**）：
#: 时间量以秒计（宿主内部 `t1_us` 先验会先换算成秒），比量无量纲。
QISKIT_HOST_UNITS = {
    "time": "s",
    "freq": "Hz",
    "voltage": "V",
    "power": "dBm",
    "ratio": "",
}


def _to_quantity(name: str, value: float) -> Optional[Dict[str, Any]]:
    """宿主扁平指标 → 规范数量（`{"value", "unit"}`）。

    - 名字在 `qwf/quantities.QUANTITIES` 里 → 按 QISKIT_HOST_UNITS 换算到规范单位；
    - 名字是 v1 扁平名（`T1_us`） → 走 `legacy_to_quantity`；
    - 认不出来 → None（原始值仍在 `artifacts["metrics"]` 里）。
    """
    legacy = quantities.legacy_to_quantity(name, value)
    if legacy:
        return legacy
    dimension = quantities.dimension_of(name)
    if dimension is None:
        return None
    unit = QISKIT_HOST_UNITS.get(dimension, "")
    try:
        return quantities.normalize({"value": value, "unit": unit}, name)
    except Exception:  # noqa: BLE001 — 单位不认识就不换算，别把结果搞坏
        return None


class QiskitAdapter(BackendAdapter):
    """把 DSL 的实验请求转发给宿主的 qiskit 服务。

    只用标准库 `urllib.request`；构造与探测都不做 IO（`health()` /
    `capabilities()` 才发请求，且**永不抛异常**——服务没起来时返回
    "不可用 + 原因"，让上层能优雅降级）。
    """

    name = "qiskit"
    label = "Qiskit（转发到宿主 qiskit 服务）"
    IMPLEMENTED = True

    def __init__(self, base_url: str = "", timeout: float = 30.0,
                 probe_timeout: float = 10.0,
                 map_quantities: bool = False,
                 logger: Optional[Callable[[str], None]] = None):
        url = base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        self.base_url = str(url).rstrip("/")
        self.default_timeout = float(timeout or 30.0)
        # 探测（health / experiments）用更短的超时：它们在执行路径上，
        # 服务没起来时要**尽快**返回"不可用"，而不是把节点卡住。
        self.probe_timeout = float(probe_timeout or 10.0)
        # 是否把宿主的扁平指标换算成规范数量。默认**关**：
        # 宿主 qiskit 的结果是 `qiskit_normalized`（时间以秒计、量纲无量纲），
        # 换算成规范单位（us 等）是一次约定，不是事实 ⇒ 默认只把原始指标
        # 放进 artifacts["metrics"]，`quantities` 留空，绝不编造单位。
        self.map_quantities = bool(map_quantities)
        self._log = logger or (lambda _msg: None)

    # ── HTTP 面（标准库；任何失败都转成结构化结果，不抛） ─────────────────
    def _request(self, path: str, method: str = "GET",
                 payload: Optional[Dict[str, Any]] = None,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        """发一次 HTTP 请求，返回 `{"ok", "status", "data"|"error"}`。**不抛异常。**"""
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=float(timeout or self.default_timeout)) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                code = int(getattr(resp, "status", 200) or 200)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — 读错误体失败不影响结论
                detail = ""
            message = _error_message(detail) or f"HTTP {exc.code}"
            return {"ok": False, "status": int(exc.code), "error": message, "body": detail}
        except Exception as exc:  # noqa: BLE001 — 连接失败/超时/DNS 都要能报出来
            return {"ok": False, "status": 0,
                    "error": f"{type(exc).__name__}: {exc}"}
        try:
            parsed = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": code,
                    "error": f"响应不是合法 JSON: {type(exc).__name__}: {exc}",
                    "body": raw[:500]}
        return {"ok": True, "status": code, "data": parsed}

    # ── 能力声明 ───────────────────────────────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        """实验覆盖由宿主给出（`GET /qiskit/experiments`）。

        服务不可达时**不抛异常**，返回 `{"available": False, "reason": ...}`，
        其余字段保持同一形状（空列表），上层照样能读。
        """
        resp = self._request("/qiskit/experiments", "GET", timeout=self.probe_timeout)
        if not resp["ok"]:
            reason = f"qiskit 实验目录不可达：{resp['error']}"
            return {
                "vendor": self.name,
                "available": False,
                "reason": reason,
                "error": resp["error"],
                "base_url": self.base_url,
                "kind": "simulator",
                "experiments": [],
                "arities": {},
                "experiment_params": {},
                "approximations": {},
                "fitters": [],
                "sweeps": [],
                "control": [],
                "missing": [],
                "caps": [],
            }
        data = resp["data"] if isinstance(resp["data"], dict) else {}
        specs = [s for s in (data.get("experiments") or []) if isinstance(s, dict)]
        names = [str(s.get("name")) for s in specs if s.get("name")]
        coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
        return {
            "vendor": self.name,
            "available": True,
            "kind": "simulator",
            "base_url": self.base_url,
            "unit_system": data.get("unit_system") or "qiskit_normalized",
            "experiments": names,
            # arity：宿主实验目录没有这个字段，按本地表补齐（默认 1 = 单比特）
            "arities": {n: EXPERIMENT_ARITY.get(n, 1) for n in names},
            # 每个实验认哪些参数名（宿主 spec 声明的）
            "experiment_params": {
                str(s.get("name")): sorted((s.get("params") or {}).keys())
                for s in specs if s.get("name")
            },
            # 每个实验是原生态(exact)还是等效模型(model)
            "approximations": {
                str(s.get("name")): s.get("approximation")
                for s in specs if s.get("name")
            },
            "fitters": [],          # 拟合在 analysis 侧
            "sweeps": ["abs"],      # 扫描由宿主实验 spec 的 points 决定
            "control": [],          # 同步执行，无异步 job / 取消语义
            "coverage": coverage,
            "missing": list(coverage.get("missing_in_qiskit") or []),
            "extra": list(coverage.get("extra_in_qiskit") or []),
            "note": "能力来自宿主 qiskit 实验目录；模拟器不写设备参数，拟合在 analysis 侧",
        }

    def health(self) -> Dict[str, Any]:
        """可达性探测（`GET /qiskit/health`）。**永不抛异常。**"""
        resp = self._request("/qiskit/health", "GET", timeout=self.probe_timeout)
        if not resp["ok"]:
            reason = f"qiskit 服务不可达：{resp['error']}"
            return {
                "ok": False,
                "available": False,
                "backend": self.name,
                "vendor": self.name,
                "reason": reason,
                "error": resp["error"],
                "base_url": self.base_url,
            }
        data = resp["data"] if isinstance(resp["data"], dict) else {}
        available = bool(data.get("available"))
        return {
            "ok": available,
            "available": available,
            "backend": self.name,
            "vendor": self.name,
            "reason": "" if available else (str(data.get("error") or "宿主 qiskit 后端不可用")),
            "error": data.get("error"),
            "base_url": self.base_url,
            "versions": data.get("versions") or {},
            "execution": data.get("execution"),
            "unit_system": data.get("unit_system"),
            "experiments": data.get("experiments"),
            "detail": data,
        }

    # ── 执行 ───────────────────────────────────────────────────────────────
    def _experiment_name(self, experiment: str) -> str:
        """IR 实验名 → 宿主 qiskit 实验名（去掉 `sq.` 前缀 + 别名表）。"""
        name = str(experiment or "").strip()
        if name.startswith("sq."):
            name = name[3:]
        return EXPERIMENT_ALIASES.get(name, name)

    def _qubits(self, req: RunRequest) -> List[str]:
        """这次实验用哪些比特。

        顺序优先级：
        1. `RunRequest.qubits`（**上游 A1 组待补**；用 `getattr` 兼容，
           补上后这里自动生效，不用改本文件）；
        2. `RunRequest.options["qubits"]`（适配器私有开关，现在就能用）；
        3. `RunRequest.qubit`（单数）。
        """
        names: List[str] = []
        raw = getattr(req, "qubits", None)
        if isinstance(raw, (list, tuple)):
            names = [str(q).strip() for q in raw if str(q).strip()]
        if not names:
            opt = (req.options or {}).get("qubits")
            if isinstance(opt, (list, tuple)):
                names = [str(q).strip() for q in opt if str(q).strip()]
            elif isinstance(opt, str) and opt.strip():
                names = [opt.strip()]
        if not names and req.qubit:
            names = [str(req.qubit).strip()]
        return names

    def _params(self, req: RunRequest) -> Tuple[Dict[str, Any], List[str]]:
        """`RunRequest.params` → 宿主 `params`（裸值）。

        - `{"value": x, "unit": "us"}` → `x`（宿主按 spec 收裸数，单位换算不在这里做）；
        - 标量/字符串/数值列表原样保留；
        - 其它结构（扫描轴这类 dict）**不传**：宿主按 spec 过滤，传了也会被丢，
          丢掉的名字记在 `dropped` 里，进 `meta` 供排查。
        """
        out: Dict[str, Any] = {}
        dropped: List[str] = []
        for key, value in (req.params or {}).items():
            name = str(key)
            if isinstance(value, dict):
                if "value" in value:
                    out[name] = value.get("value")
                    continue
                dropped.append(name)
                continue
            if isinstance(value, (list, tuple)):
                if all(_is_number(v) for v in value):
                    out[name] = list(value)
                    continue
                dropped.append(name)
                continue
            out[name] = value
        return out, dropped

    def _seed(self, req: RunRequest) -> Optional[int]:
        """随机种子：`RunRequest.seed`（**上游 A1 组待补**）→ `options["seed"]`。"""
        raw = getattr(req, "seed", None)
        if raw is None:
            raw = (req.options or {}).get("seed")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _build_payload(self, req: RunRequest) -> Tuple[Dict[str, Any], List[str]]:
        """`RunRequest` → `POST /qiskit/run` 的请求体。

        返回 `(payload, dropped)`：`dropped` 是**没能传给宿主**的参数名
        （结构不是标量），只进 `meta`，不混进请求体。
        """
        names = self._qubits(req)
        params, dropped = self._params(req)
        payload: Dict[str, Any] = {
            "experiment": self._experiment_name(req.experiment),
            "qubit": names[0] if names else "",
            "params": params,
        }
        if names:
            payload["qubits"] = names
        if req.shots is not None:
            try:
                payload["shots"] = int(req.shots)
            except (TypeError, ValueError):
                pass
        seed = self._seed(req)
        if seed is not None:
            payload["seed"] = seed
        circuit = str((req.options or {}).get("circuit") or "").strip()
        if circuit:
            payload["circuit"] = circuit
        return payload, dropped

    def run(self, req: RunRequest, token: Optional[CancelToken] = None) -> RawResult:
        """把一次实验请求转发给宿主（`POST /qiskit/run`）。"""
        if token is not None and token.is_set():
            return RawResult(ok=False, status="cancelled", error="cancelled before submit",
                             meta={"vendor": self.name})
        payload, dropped = self._build_payload(req)
        timeout = req.timeout or self.default_timeout
        resp = self._request("/qiskit/run", "POST", payload, timeout=timeout)
        if not resp["ok"]:
            self._log(f"[qiskit] run 请求失败：{resp['error']}")
            return RawResult(
                ok=False,
                status="failed",
                error=str(resp["error"]),
                meta={"vendor": self.name, "request": payload},
            )
        return self._to_raw_result(resp["data"], payload, dropped)

    def _to_raw_result(self, data: Any, payload: Dict[str, Any],
                       dropped: Optional[List[str]] = None) -> RawResult:
        """宿主信封 → `RawResult`（厂商 id 只进 `meta`）。"""
        if not isinstance(data, dict):
            return RawResult(ok=False, status="failed",
                             error=f"qiskit 返回了非 dict 响应: {type(data).__name__}",
                             meta={"vendor": self.name, "request": payload})
        status = str(data.get("status") or "").lower()
        ok = status == STATUS_COMPLETED
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        # 数量：宿主给的是扁平指标（`T1` / `contrast` …），单位制是
        # `qiskit_normalized`。**默认不换算**（换算单位是一次约定，不是事实），
        # 原始指标一律留在 artifacts["metrics"]；只有显式开了 map_quantities
        # 才按"宿主时间量以秒计"的约定换算到规范单位。
        quantities_out: Dict[str, Dict[str, Any]] = {}
        if self.map_quantities:
            for name, value in metrics.items():
                if not _is_number(value):
                    continue
                mapped = _to_quantity(str(name), float(value))
                if mapped:
                    quantities_out[str(name)] = mapped
        artifacts: Dict[str, Any] = {"metrics": metrics}
        if data.get("data"):
            artifacts["data"] = data.get("data")
        if data.get("plotPath"):
            artifacts["plotPath"] = data.get("plotPath")
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
        meta = {
            "vendor": self.name,
            "native_id": data.get("task_id"),
            "simulated": True,
            "unit_system": data.get("unit_system") or "qiskit_normalized",
            "approximation": data.get("approximation"),
            "experiment": data.get("experiment") or payload.get("experiment"),
            "qubits": extra.get("qubits") or payload.get("qubits"),
            "elapsed_ms": data.get("elapsed_ms"),
            "request": payload,
        }
        if dropped:
            meta["dropped_params"] = list(dropped)
        if self.map_quantities:
            meta["quantity_convention"] = (
                "宿主 qiskit 归一化单位 → 规范单位（时间为秒→us 等）；仅供对照，不是标定值"
            )
        return RawResult(
            ok=ok,
            status="completed" if ok else (status or "failed"),
            data_ref=data.get("task_id"),
            quantities=quantities_out,
            artifacts=artifacts,
            stdout=str(data.get("stdout") or ""),
            error="" if ok else str(data.get("error") or f"qiskit status={status}"),
            meta=meta,
        )

    # ── 设备与参数（模拟器没有真机语义） ───────────────────────────────────
    def devices(self) -> List[Dict[str, Any]]:
        """模拟器没有设备树。"""
        return []

    def read_param(self, path: str) -> Dict[str, Any]:
        raise NotImplementedError(
            "qiskit 后端没有设备参数可读：模拟器无寄存器/无标定值，"
            "参数读写在 lqcs 后端；本适配器只转发模拟执行"
        )

    def write_param(self, path: str, quantity: Dict[str, Any]) -> None:
        raise NotImplementedError(
            "qiskit 后端不写设备参数：模拟器无物理副作用，"
            "写参数在 lqcs 后端；本适配器只转发模拟执行"
        )

    # ── 静态语义（编译期统计用） ───────────────────────────────────────────
    def write_effects(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """模拟器**不改任何真机参数** ⇒ 没有写副作用。

        形状与 `qwf/effects.py::write_effects` 一致（`fn` / `mode` / `writes` /
        `effective` / `reason`），核心层照样能消费。
        """
        return {
            "fn": str(fn or ""),
            "mode": "none",
            "writes": [],
            "effective": False,
            "reason": "模拟器不写真机参数",
        }

    def estimate_runtime(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """粗估运行时长（模拟器，无真机排队）。

        只是"不拦"用的量级参考：`0.2s + shots/20000 * points`，
        误差可以到数倍，`confidence="rough"`。
        """
        params = params or {}
        try:
            shots = int(params.get("shots") or 2000)
        except (TypeError, ValueError):
            shots = 2000
        try:
            points = int(params.get("points") or 1)
        except (TypeError, ValueError):
            points = 1
        seconds = 0.2 + shots / 20000.0 * max(1, points)
        return {
            "fn": str(fn or ""),
            "seconds": round(seconds, 3),
            "points": max(1, points),
            "adaptive": False,
            "confidence": "rough",
            "source": "qiskit 模拟粗估（无实测数据）",
        }

    def write_key(self, name: str, path: Optional[List[str]] = None) -> Optional[str]:
        """模拟器不写参数 ⇒ 没有写入键。"""
        return None

    def drift_threshold(self, key: str) -> Optional[Dict[str, Any]]:
        """模拟器不改参数 ⇒ 没有漂移看护。"""
        return None

    # ── 拟合（不在本适配器） ───────────────────────────────────────────────
    def fit(self, kind: str, data_ref: Any) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError(
            "qiskit 后端不做拟合：拟合在 analysis 侧（宿主 qter.fitData / "
            "analysis_service），本适配器只转发模拟执行并返回指标"
        )


def _error_message(detail: str) -> str:
    """从错误响应体里抠出人话（宿主错误信封是 `{"error": ...}`）。"""
    text = str(detail or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001 — 不是 JSON 就原样返回
        return text[:200]
    if isinstance(parsed, dict):
        for key in ("error", "message", "summary"):
            value = parsed.get(key)
            if value:
                return str(value)[:200]
    return text[:200]


__all__ = ["QiskitAdapter", "EXPERIMENT_ALIASES", "EXPERIMENT_ARITY",
           "DEFAULT_BASE_URL", "ENV_BASE_URL"]

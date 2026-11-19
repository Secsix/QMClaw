"""services/quantum_service/backend_policy.py —— 实验后端的选择策略

quantum_service 有三条"执行实验"的路。它们不是互斥的"模式"，而是**一条按顺序
尝试的链**，第一个可用的胜出：

==  ==============  ==========  ==========================================
#   名称            类型        说明
==  ==============  ==========  ==========================================
1   ``lqcs``        hardware    真机通道（LabRAD + lqms），唯一的物理设备出口
2   ``qiskit``      simulator   Aer 本地模拟，不需要任何实验室依赖
3   ``offline_sim`` replay      历史数据 / 合成数据回放（offline_simulator）
==  ==============  ==========  ==========================================


为什么需要这个模块
------------------
改造前，"用不用真机"由 ``server._is_offline_mode`` 直接调用
``_ensure_connected()`` 判断，而那是一次**真实的连接尝试**：

* 在没装 lqms 的机器上，它会连试若干次、每次都打一大段 traceback；
* 它还在 ``_mode_lock`` 里执行，连接期间所有请求排队，服务看起来像卡死。

本模块把**探测**和**使用**分开：探测必须是毫秒级、可缓存、永不抛异常的；
真正的连接仍然由各后端自己负责（``LabRADClient`` / ``qiskit_backend``）。


唯一配置
--------
``config/fallback_config.json`` → ``quantum_service.backends``::

    "backends": {
      "order": ["lqcs", "qiskit", "offline_sim"],
      "disabled": [],
      "probe_ttl_s": 30,
      "qiskit_probe_ttl_s": 300
    }

``mode``（online / offline / auto）**保留为兼容别名**，不新增第二套开关：

* ``online``  → ``["lqcs"]``         严格真机，与改造前完全一致
* ``offline`` → ``["offline_sim"]``  与改造前完全一致
* ``auto``    → 配置里的 ``order``   （相比改造前新增了 qiskit 这一层）

所以默认配置下：真机行为一字未改；没有 lqcs 的演示机自动落到 qiskit。
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

# ---------------------------------------------------------------- 常量

BACKEND_LQCS = "lqcs"
BACKEND_QISKIT = "qiskit"
BACKEND_OFFLINE = "offline_sim"

ALL_BACKENDS = (BACKEND_LQCS, BACKEND_QISKIT, BACKEND_OFFLINE)

BACKEND_KINDS: Dict[str, str] = {
    BACKEND_LQCS: "hardware",
    BACKEND_QISKIT: "simulator",
    BACKEND_OFFLINE: "replay",
}

BACKEND_LABELS: Dict[str, str] = {
    BACKEND_LQCS: "真机（LabRAD）",
    BACKEND_QISKIT: "qiskit 本地模拟",
    BACKEND_OFFLINE: "离线数据回放",
}

DEFAULT_ORDER: List[str] = [BACKEND_LQCS, BACKEND_QISKIT, BACKEND_OFFLINE]

#: mode → 固定顺序。不在表里的（None / "auto"）使用配置里的 order。
MODE_ORDERS: Dict[str, List[str]] = {
    "online": [BACKEND_LQCS],
    "offline": [BACKEND_OFFLINE],
}

DEFAULT_PROBE_TTL_S = 30.0
#: qiskit 探测可能要起子进程（约 1.5s），缓存久一点
DEFAULT_QISKIT_PROBE_TTL_S = 300.0
#: lqms 不在时 TCP 探测没有意义，但 lqms 在时必须给它一个短超时
DEFAULT_TCP_TIMEOUT_S = 2.0


# ---------------------------------------------------------------- 状态


@dataclass
class BackendState:
    """一个后端的可用性快照（``checked_at`` 为 monotonic 秒）"""

    name: str
    kind: str
    available: bool
    reason: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    checked_at: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "label": BACKEND_LABELS.get(self.name, self.name),
            "available": self.available,
            "reason": self.reason,
            "detail": self.detail,
        }


# ---------------------------------------------------------------- 探测工具


def _default_log(message: str) -> None:
    """默认日志：带 ``[quantum_service]`` 前缀，网关的日志过滤器才放行（约定 9）"""
    try:
        print(f"[quantum_service] {message}", file=sys.stderr, flush=True)
    except Exception:
        pass


def module_available(module: str) -> bool:
    """``find_spec`` 级快筛：毫秒级、不导入模块、不触发副作用。

    这是整个策略能"便宜"的关键——没装 lqms 的机器连一次连接都不用试。
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _tcp_reachable(host: str, port: int, timeout: float) -> tuple[bool, Optional[str]]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _labrad_endpoint() -> tuple[Optional[str], Optional[int], Optional[str]]:
    """从 ``services.common.config`` 取 LabRAD 的 host/port；取不到不抛异常。"""
    try:
        from services.common.config import get_labrad_params

        params = get_labrad_params() or {}
        return params.get("host"), params.get("port"), None
    except Exception as exc:  # 配置缺失不该让探测炸掉
        return None, None, f"{type(exc).__name__}: {exc}"


def probe_lqcs(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = DEFAULT_TCP_TIMEOUT_S,
    lqms_module: str = "lqms",
) -> BackendState:
    """真机通道探测：先看 Python 包，再看 TCP 端口。

    两级的意义：没有 lqms 的机器（演示机、纯前端开发机）**一次连接都不试**；
    装了 lqms 但 LabRAD 没起时，用一次短超时的 TCP 探测快速判定。
    """
    if not module_available(lqms_module):
        return BackendState(
            name=BACKEND_LQCS,
            kind=BACKEND_KINDS[BACKEND_LQCS],
            available=False,
            reason=f"未安装 {lqms_module}（本机没有 lqcs 真机栈）",
            detail={"lqms_installed": False},
        )

    cfg_host, cfg_port, cfg_error = _labrad_endpoint()
    host = host or cfg_host or "127.0.0.1"
    if port is None:
        port = cfg_port or 7682

    ok, error = _tcp_reachable(host, int(port), timeout)
    detail: Dict[str, Any] = {
        "lqms_installed": True,
        "labrad_host": host,
        "labrad_port": int(port),
        "labrad_reachable": ok,
    }
    if cfg_error:
        detail["config_error"] = cfg_error
    return BackendState(
        name=BACKEND_LQCS,
        kind=BACKEND_KINDS[BACKEND_LQCS],
        available=ok,
        reason=None if ok else f"LabRAD 连不上 {host}:{port}（{error}）",
        detail=detail,
    )


def probe_qiskit(status_fn: Optional[Callable[[], Dict[str, Any]]] = None) -> BackendState:
    """qiskit 探测：委托 ``qiskit_backend.status()``（它自己会挑对解释器）。"""
    if status_fn is None:
        try:
            from services.quantum_service import qiskit_backend

            status_fn = qiskit_backend.status
        except Exception as exc:
            return BackendState(
                name=BACKEND_QISKIT,
                kind=BACKEND_KINDS[BACKEND_QISKIT],
                available=False,
                reason=f"无法加载 qiskit_backend：{type(exc).__name__}: {exc}",
            )
    try:
        status = status_fn() or {}
    except Exception as exc:
        return BackendState(
            name=BACKEND_QISKIT,
            kind=BACKEND_KINDS[BACKEND_QISKIT],
            available=False,
            reason=f"探测失败：{type(exc).__name__}: {exc}",
        )

    available = bool(status.get("available"))
    reason: Optional[str] = None
    if not available:
        reason = status.get("error") or status.get("install_hint") or "qiskit 不可用"
    return BackendState(
        name=BACKEND_QISKIT,
        kind=BACKEND_KINDS[BACKEND_QISKIT],
        available=available,
        reason=reason,
        detail=status,
    )


def probe_offline_sim(
    provider_ready: bool,
    *,
    data_path: Optional[str] = None,
    reason: Optional[str] = None,
) -> BackendState:
    """离线回放探测：只依赖进程内是否拿到了 ``OfflineDataProvider``。"""
    detail: Dict[str, Any] = {"provider_ready": bool(provider_ready)}
    if data_path:
        detail["data_path"] = data_path
    return BackendState(
        name=BACKEND_OFFLINE,
        kind=BACKEND_KINDS[BACKEND_OFFLINE],
        available=bool(provider_ready),
        reason=None if provider_ready else (reason or "离线数据提供器未就绪（data/offline_data 为空？）"),
        detail=detail,
    )


# ---------------------------------------------------------------- 策略


def _normalize_order(order: Optional[Iterable[str]]) -> List[str]:
    """只保留已知后端名，去重保序；空/非法则退回默认顺序。"""
    if not order:
        return list(DEFAULT_ORDER)
    seen: List[str] = []
    for name in order:
        if isinstance(name, str) and name in ALL_BACKENDS and name not in seen:
            seen.append(name)
    return seen or list(DEFAULT_ORDER)


class BackendPolicy:
    """后端顺序解析 + 探测缓存 + 运行期偏好。

    线程安全：``resolve`` / ``prefer`` / ``report`` 走同一把锁；实际探测在锁外
    进行（每后端各自一把锁防重复探测），因此一个慢后端不会卡住其它后端的查询。
    """

    def __init__(
        self,
        order: Optional[Iterable[str]] = None,
        disabled: Optional[Iterable[str]] = None,
        *,
        probe_ttl_s: float = DEFAULT_PROBE_TTL_S,
        qiskit_probe_ttl_s: float = DEFAULT_QISKIT_PROBE_TTL_S,
        probes: Optional[Dict[str, Callable[[], BackendState]]] = None,
        logger: Optional[Callable[[str], None]] = None,
    ):
        self._order = _normalize_order(order)
        self._disabled = {n for n in (disabled or []) if n in ALL_BACKENDS}
        self._probe_ttl = float(probe_ttl_s)
        self._qiskit_ttl = float(qiskit_probe_ttl_s)
        self._probes: Dict[str, Callable[[], BackendState]] = dict(probes or {})
        self._cache: Dict[str, BackendState] = {}
        self._lock = threading.RLock()
        self._probe_locks: Dict[str, threading.Lock] = {n: threading.Lock() for n in ALL_BACKENDS}
        self._preferred: Optional[str] = None
        self._log = logger or _default_log
        self._last_logged: Dict[str, bool] = {}

    # ------------------------------------------------------------ 构造

    @classmethod
    def from_config(
        cls,
        config: Optional[Dict[str, Any]],
        *,
        probes: Optional[Dict[str, Callable[[], BackendState]]] = None,
        logger: Optional[Callable[[str], None]] = None,
    ) -> "BackendPolicy":
        """从 ``fallback_config.json`` 的 ``quantum_service.backends`` 构造。"""
        config = config or {}
        return cls(
            order=config.get("order"),
            disabled=config.get("disabled"),
            probe_ttl_s=float(config.get("probe_ttl_s", DEFAULT_PROBE_TTL_S)),
            qiskit_probe_ttl_s=float(config.get("qiskit_probe_ttl_s", DEFAULT_QISKIT_PROBE_TTL_S)),
            probes=probes,
            logger=logger,
        )

    # ------------------------------------------------------------ 顺序

    def resolve(self, mode: Optional[str] = None) -> List[str]:
        """给定 ``mode``，返回要依次尝试的后端名列表。

        ``online`` / ``offline`` 是硬指定，**不受运行期偏好影响**（否则"严格真机"
        就不严格了）；只有 ``auto``（或未指定）才把偏好提到最前。
        """
        with self._lock:
            fixed = MODE_ORDERS.get((mode or "").strip().lower())
            if fixed:
                order = list(fixed)
            else:
                order = list(self._order)
                if self._preferred and self._preferred not in self._disabled:
                    order = [self._preferred] + [n for n in order if n != self._preferred]
            return [n for n in order if n not in self._disabled]

    @property
    def preferred(self) -> Optional[str]:
        with self._lock:
            return self._preferred

    @property
    def disabled(self) -> List[str]:
        with self._lock:
            return sorted(self._disabled)

    @property
    def order(self) -> List[str]:
        with self._lock:
            return list(self._order)

    def prefer(self, name: Optional[str]) -> None:
        """把某后端提到 auto 顺序的最前；传 ``None`` 清除偏好。"""
        if name is not None and name not in ALL_BACKENDS:
            raise ValueError(f"未知后端：{name!r}，可选 {list(ALL_BACKENDS)}")
        with self._lock:
            self._preferred = name
        self._log(f"[backend] 运行期偏好设为 {name or '（已清除）'}")

    def clear_preference(self) -> None:
        self.prefer(None)

    def set_disabled(self, names: Iterable[str]) -> None:
        """运维开关：临时排除某些后端（不写回配置文件）。"""
        with self._lock:
            self._disabled = {n for n in names if n in ALL_BACKENDS}

    # ------------------------------------------------------------ 探测

    def _ttl_for(self, name: str) -> float:
        return self._qiskit_ttl if name == BACKEND_QISKIT else self._probe_ttl

    def state(self, name: str, *, force: bool = False) -> BackendState:
        """返回某后端的状态；命中缓存则直接返回，否则探测一次。"""
        if name not in ALL_BACKENDS:
            return BackendState(name=name, kind="unknown", available=False, reason="未知后端")

        with self._lock:
            cached = self._cache.get(name)
        if cached is not None and not force and (time.monotonic() - cached.checked_at) < self._ttl_for(name):
            return cached

        # 同一后端只允许一个线程探测（qiskit 探测可能起子进程）
        lock = self._probe_locks.setdefault(name, threading.Lock())
        if not lock.acquire(blocking=False):
            # 别的线程正在探，先用旧快照（没有就等它）
            with self._lock:
                cached = self._cache.get(name)
            if cached is not None:
                return cached
            lock.acquire()
        try:
            with self._lock:
                cached = self._cache.get(name)
            if cached is not None and not force and (time.monotonic() - cached.checked_at) < self._ttl_for(name):
                return cached

            probe = self._probes.get(name)
            if probe is None:
                state = BackendState(
                    name=name,
                    kind=BACKEND_KINDS.get(name, "unknown"),
                    available=False,
                    reason="未注册探测函数",
                )
            else:
                try:
                    state = probe()
                except Exception as exc:  # 探测永不让调用方炸
                    state = BackendState(
                        name=name,
                        kind=BACKEND_KINDS.get(name, "unknown"),
                        available=False,
                        reason=f"探测异常：{type(exc).__name__}: {exc}",
                    )
            state.checked_at = time.monotonic()
            with self._lock:
                self._cache[name] = state
            self._log_transition(state)
            return state
        finally:
            lock.release()

    def _log_transition(self, state: BackendState) -> None:
        """只在可用性**发生变化**时打一条，避免每请求刷屏（约定 6 的思路）。"""
        previous = self._last_logged.get(state.name)
        if previous == state.available:
            return
        self._last_logged[state.name] = state.available
        if state.available:
            self._log(f"[backend] {state.name} 可用（{state.kind}）")
        else:
            self._log(f"[backend] {state.name} 不可用：{state.reason}")

    def invalidate(self, name: Optional[str] = None) -> None:
        with self._lock:
            if name is None:
                self._cache.clear()
                self._last_logged.clear()
            else:
                self._cache.pop(name, None)
                self._last_logged.pop(name, None)

    # ------------------------------------------------------------ 决策

    def active(self, mode: Optional[str] = None) -> Optional[str]:
        """按顺序找第一个可用的后端；全不可用返回 ``None``。"""
        for name in self.resolve(mode):
            if self.state(name).available:
                return name
        return None

    def peek_active(self, mode: Optional[str] = None) -> Optional[str]:
        """只用**已缓存**的探测结果判断，绝不触发新探测。

        给高频的 ``/health`` 用：普通请求不该因为一次探测（qiskit 可能起子进程、
        lqcs 可能做 TCP 超时）而变慢。没有缓存时返回 ``None``（= 尚未判定）。
        """
        with self._lock:
            for name in self.resolve(mode):
                cached = self._cache.get(name)
                if cached is not None and cached.available:
                    return name
        return None

    def cache_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """已缓存的各后端状态快照（不探测），键为后端名。"""
        with self._lock:
            return {name: state.as_dict() for name, state in self._cache.items()}

    def is_hardware(self, mode: Optional[str] = None) -> bool:
        """当前生效的是不是真机通道（即"要不要碰物理设备"）。"""
        return self.active(mode) == BACKEND_LQCS

    def report(self, mode: Optional[str] = None, *, force: bool = False) -> Dict[str, Any]:
        """给 ``GET /backends`` 用的完整快照。"""
        order = self.resolve(mode)
        states = [self.state(name, force=force) for name in order]
        active = next((s.name for s in states if s.available), None)
        return {
            "mode": mode,
            "order": order,
            "active": active,
            "active_kind": BACKEND_KINDS.get(active or "", None),
            "active_label": BACKEND_LABELS.get(active or "", None),
            "hardware": active == BACKEND_LQCS,
            "preferred": self.preferred,
            "disabled": self.disabled,
            "backends": [s.as_dict() for s in states],
            "checked_at": time.time(),
        }

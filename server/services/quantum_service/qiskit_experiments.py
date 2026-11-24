"""
services/quantum_service/qiskit_experiments.py — Qiskit 实验目录与调度

**这个模块刻意不 import qiskit。**

量子服务跑在 `lqcs` 环境里，那个环境不一定装了 qiskit；就算没装，
服务也必须能正常起来（真机在线/离线模式跟 qiskit 无关）。
所以这里只放：

- 数据结构（请求 / 产物 / 实验声明）
- 实验目录（14 个实验的元数据 + 参数说明）
- 一个**懒加载**调度器：真正执行时才去 import `qiskit_circuits`

需要 qiskit 的线路构造与拟合全部在 `qiskit_circuits.py`。

**实验词表与真机共用**：`config/experiment_configs.json` 里的 14 个实验名
在 qiskit 后端里是**同名**的，agent / workflow / 前端不需要为 qiskit 另开一套命名。
`list_specs()` 会把两个来源交叉起来，让调用方一眼看出
"这个实验真机上能不能做"（`available`）和"qiskit 里是原生态还是等效模型"
（`approximation`）。

**单位制**：qiskit 里 ħ=1，时间/频率都是无量纲的。有物理单位的量
（T1/T2/µs/Hz）靠入参里的先验换算，产出的结果统一打上
`unit_system: "qiskit_normalized"` 标记，防止被当成真机标定值误用。
"""

from __future__ import annotations

import importlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 结果的单位制标记：凡是 qiskit 后端产出的数据都带这个。
UNIT_SYSTEM = "qiskit_normalized"
BACKEND_NAME = "qiskit"

# 实现所在模块（需要 qiskit），由 run_experiment 懒加载。
_IMPL_MODULE = "qiskit_circuits"


class QiskitUnavailable(RuntimeError):
    """qiskit 后端不可用（没装 / 装坏了 / 版本不对）。

    调用方应当把它翻译成**明确的用户错误**（而不是 HTTP 500），
    因为这不是服务故障，是"这台机器没配这个后端"。
    """


@dataclass
class QiskitRequest:
    """一次 qiskit 执行请求。"""

    experiment: str
    qubit: str = ""
    qubits: List[str] = field(default_factory=list)   # 多比特（2026-11-19）
    params: Dict[str, Any] = field(default_factory=dict)
    shots: int = 2000
    seed: int = 42

    @property
    def qubit_list(self) -> List[str]:
        """有效比特列表：`qubits` 优先，否则退回单数的 `qubit`。"""
        names = [str(q) for q in (self.qubits or []) if str(q)]
        if not names and self.qubit:
            names = [str(self.qubit)]
        return names

    def p(self, name: str, default: Any) -> Any:
        """取参数；没传或为空时用默认值。"""
        value = self.params.get(name, default)
        if value is None:
            return default
        return value

    def pf(self, name: str, default: float) -> float:
        """取浮点参数，非法值退回默认。"""
        try:
            out = float(self.params.get(name, default))
        except (TypeError, ValueError):
            return float(default)
        if not math.isfinite(out):
            return float(default)
        return out

    def pi(self, name: str, default: int) -> int:
        """取正整数参数，非法值退回默认。"""
        try:
            out = int(self.params.get(name, default))
        except (TypeError, ValueError):
            return int(default)
        return out if out > 0 else int(default)


@dataclass
class ExperimentOutcome:
    """一个实验跑完之后的产物。"""

    x: List[float] = field(default_factory=list)
    y: List[float] = field(default_factory=list)
    xlabel: str = "X"
    ylabel: str = "Y"
    metrics: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)
    plot_type: str = "line"


@dataclass
class ExperimentSpec:
    """一个实验的声明：能力要**声明**，而不是靠调用失败来发现。"""

    name: str
    title: str
    approximation: str          # exact | model
    description: str
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    impl: str = ""              # "qiskit_circuits:_build_t1"


EXPERIMENT_REGISTRY: Dict[str, ExperimentSpec] = {}


def _register(spec: ExperimentSpec) -> None:
    EXPERIMENT_REGISTRY[spec.name] = spec


_register(ExperimentSpec(
    name="t1",
    title="T1 Relaxation",
    approximation="exact",
    description="振幅衰减通道测弛豫时间；线路即物理过程。",
    params={
        "t1_us": {"default": 50.0, "unit": "us", "desc": "T1 先验值（定标用）"},
        "points": {"default": 31, "unit": "", "desc": "扫描点数"},
        "t_max_us": {"default": None, "unit": "us", "desc": "最大延迟，默认 5×t1_us"},
    },
    impl=f"{_IMPL_MODULE}:_build_t1",
))

_register(ExperimentSpec(
    name="ramsey",
    title="Ramsey",
    approximation="exact",
    description="H→phase→H 干涉条纹，热弛豫通道给出 T2*。",
    params={
        "t2_us": {"default": 20.0, "unit": "us", "desc": "T2* 先验值"},
        "t1_us": {"default": 60.0, "unit": "us", "desc": "T1 先验值（热弛豫通道用，默认 3×t2）"},
        "detuning_hz": {"default": 0.0, "unit": "Hz", "desc": "驱动失谐"},
        "points": {"default": 41, "unit": "", "desc": "扫描点数"},
        "t_max_us": {"default": None, "unit": "us", "desc": "最大延迟，默认 5×t2_us"},
    },
    impl=f"{_IMPL_MODULE}:_build_ramsey",
))

_register(ExperimentSpec(
    name="piamp",
    title="Pi Pulse Amplitude",
    approximation="exact",
    description="Rabi 振荡扫描定标 π 脉冲幅度（幅度→转角用饱和模型）。",
    params={
        "amp_max": {"default": 1.0, "unit": "a.u.", "desc": "扫描上限"},
        "points": {"default": 41, "unit": "", "desc": "扫描点数"},
        "theta_sat": {"default": 6.283185307179586, "unit": "rad", "desc": "饱和转角"},
        "sat_k": {"default": 2.0, "unit": "", "desc": "饱和系数 k（θ=θ_sat·tanh(k·a)）"},
    },
    impl=f"{_IMPL_MODULE}:_build_piamp",
))

_register(ExperimentSpec(
    name="xeb",
    title="Cross-Entropy Benchmarking",
    approximation="exact",
    description="随机 Clifford 序列的实测分布与理想分布的 Bhattacharyya 保真度，按深度测衰减。",
    params={
        "cycles": {"default": 8, "unit": "", "desc": "Clifford 层数（深度扫描上限）"},
        "sequences": {"default": 12, "unit": "", "desc": "每个深度的序列条数"},
        "error_rate": {"default": 0.01, "unit": "", "desc": "退极化门误差率"},
    },
    impl=f"{_IMPL_MODULE}:_build_xeb",
))

_register(ExperimentSpec(
    name="single_shot",
    title="Single-shot Fidelity",
    approximation="exact",
    description="加读出翻转通道，统计 |0⟩/|1⟩ 制备下的读出保真度。",
    params={
        "p_readout": {"default": 0.03, "unit": "", "desc": "读出翻转概率"},
    },
    impl=f"{_IMPL_MODULE}:_build_single_shot",
))

_register(ExperimentSpec(
    name="swap",
    title="SWAP",
    approximation="exact",
    description="两比特 swap 门 + Bell 基测量，统计交换成功率。",
    params={
        "error_rate": {"default": 0.01, "unit": "", "desc": "退极化门误差率"},
    },
    impl=f"{_IMPL_MODULE}:_build_swap",
))

_register(ExperimentSpec(
    name="cz.dcz",
    title="CZ (DCZ) Gate",
    approximation="exact",
    description="两比特 CZ 门：Bell 态制备 → CZ 相位 → 奇偶校验（X 基测量）。",
    params={
        "error_rate": {"default": 0.01, "unit": "", "desc": "退极化门误差率"},
    },
    impl=f"{_IMPL_MODULE}:_build_cz",
))

_register(ExperimentSpec(
    name="spectroscopy",
    title="Spectroscopy",
    approximation="model",
    description="长脉冲 Landau-Zener 谱学等效模型，扫频找共振峰。",
    params={
        "df_hz": {"default": 5.0e6, "unit": "Hz", "desc": "扫描半宽"},
        "points": {"default": 51, "unit": "", "desc": "扫描点数"},
        "rabi_rate_hz": {"default": 1.0e6, "unit": "Hz", "desc": "Rabi 率（= 线宽 HWHM）"},
        "p_max": {"default": 1.0, "unit": "", "desc": "共振处最大跃迁概率"},
    },
    impl=f"{_IMPL_MODULE}:_build_spectroscopy",
))

_register(ExperimentSpec(
    name="pulsed_spec",
    title="Pulsed Spectroscopy",
    approximation="model",
    description="带 π 泵浦的长脉冲 Landau-Zener 谱学等效模型。",
    params={
        "df_hz": {"default": 5.0e6, "unit": "Hz", "desc": "扫描半宽"},
        "points": {"default": 51, "unit": "", "desc": "扫描点数"},
        "rabi_rate_hz": {"default": 1.0e6, "unit": "Hz", "desc": "Rabi 率（= 线宽 HWHM）"},
        "p_max": {"default": 1.0, "unit": "", "desc": "共振处最大跃迁概率"},
    },
    impl=f"{_IMPL_MODULE}:_build_pulsed_spec",
))

_register(ExperimentSpec(
    name="s21",
    title="Cavity S21",
    approximation="model",
    description="以比特跃迁概率代理腔透射，共振处下凹。",
    params={
        "df_hz": {"default": 5.0e6, "unit": "Hz", "desc": "扫描半宽"},
        "points": {"default": 51, "unit": "", "desc": "扫描点数"},
        "rabi_rate_hz": {"default": 1.0e6, "unit": "Hz", "desc": "Rabi 率（= 线宽 HWHM）"},
        "p_max": {"default": 1.0, "unit": "", "desc": "共振处最大跃迁概率"},
        "depth": {"default": 0.8, "unit": "", "desc": "透射凹陷深度"},
    },
    impl=f"{_IMPL_MODULE}:_build_s21",
))

_register(ExperimentSpec(
    name="s21_dis",
    title="S21 Dispersive Shift",
    approximation="model",
    description="比特两态下腔曲线的峰位差，即色散位移 χ。",
    params={
        "df_hz": {"default": 5.0e6, "unit": "Hz", "desc": "扫描半宽"},
        "points": {"default": 51, "unit": "", "desc": "扫描点数"},
        "rabi_rate_hz": {"default": 1.0e6, "unit": "Hz", "desc": "Rabi 率（= 线宽 HWHM）"},
        "p_max": {"default": 1.0, "unit": "", "desc": "共振处最大跃迁概率"},
        "chi_hz": {"default": 2.0e6, "unit": "Hz", "desc": "色散位移先验"},
        "depth": {"default": 0.8, "unit": "", "desc": "透射凹陷深度"},
    },
    impl=f"{_IMPL_MODULE}:_build_s21_dis",
))

_register(ExperimentSpec(
    name="iqraw",
    title="IQ Raw",
    approximation="model",
    description="单发读出的 I/Q 点云（含读出误判与相位抖动）。",
    params={
        "samples": {"default": 300, "unit": "", "desc": "每个态的样品数"},
        "p_readout": {"default": 0.03, "unit": "", "desc": "读出翻转概率"},
        "phase_sigma": {"default": 0.15, "unit": "rad", "desc": "读出相位抖动"},
    },
    impl=f"{_IMPL_MODULE}:_build_iqraw",
))

_register(ExperimentSpec(
    name="allxy",
    title="AllXY",
    approximation="model",
    description="门误差组合序列扫描，门误差用退极化噪声代理。",
    params={
        "error_rate": {"default": 0.02, "unit": "", "desc": "退极化门误差率"},
    },
    impl=f"{_IMPL_MODULE}:_build_allxy",
))

_register(ExperimentSpec(
    name="drag_calibrate",
    title="DRAG Calibration",
    approximation="model",
    description="扫 DRAG 系数取保真度最高点；泄漏用第二个比特的旗标代理。",
    params={
        "alpha_min": {"default": -1.0, "unit": "", "desc": "扫描下限"},
        "alpha_max": {"default": 1.0, "unit": "", "desc": "扫描上限"},
        "points": {"default": 41, "unit": "", "desc": "扫描点数"},
        "alpha_opt": {"default": 0.5, "unit": "", "desc": "泄漏最小时的 α 先验"},
        "leak_k": {"default": 0.08, "unit": "", "desc": "泄漏系数 k"},
        "phase_c": {"default": 0.35, "unit": "rad", "desc": "相干相位误差系数 c"},
    },
    impl=f"{_IMPL_MODULE}:_build_drag_calibrate",
))


# --------------------------------------------------------------------- 实验词表（与真机共用）


def _experiment_configs() -> Dict[str, Any]:
    """读 config/experiment_configs.json（词表与 metricsToExtract 的唯一真源）。"""
    cfg = Path(__file__).resolve().parent.parent.parent / "config" / "experiment_configs.json"
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("experiments") or {}
    except Exception:
        return {}


def list_specs() -> List[Dict[str, Any]]:
    """列出全部实验的声明（供 GET /qiskit/experiments）。

    把 qiskit 侧的声明和真机侧 experiment_configs.json 交叉起来：
    - `approximation`  : qiskit 里是原生态(exact)还是等效模型(model)
    - `available`      : 真机上这个实验能不能做（sq_nodes 有没有对应函数）
    - `metrics`        : 该实验会产出的指标名（与真机 metricsToExtract 同名）
    """
    real = _experiment_configs()
    out: List[Dict[str, Any]] = []
    for spec in EXPERIMENT_REGISTRY.values():
        ref = real.get(spec.name) or {}
        out.append({
            "name": spec.name,
            "title": spec.title,
            "approximation": spec.approximation,
            "description": spec.description,
            "params": spec.params,
            "metrics": list(ref.get("metricsToExtract") or []),
            "real_function": ref.get("function"),
            "available_on_hardware": bool(ref.get("available", True)),
            "hardware_note": ref.get("note"),
        })
    return out


def supported_experiments() -> List[str]:
    """qiskit 后端支持的实验名（与真机词表同名）。"""
    return list(EXPERIMENT_REGISTRY.keys())


def get_spec(name: str) -> Optional[ExperimentSpec]:
    return EXPERIMENT_REGISTRY.get(name)


def coverage_report() -> Dict[str, Any]:
    """对照真机词表，报告 qiskit 后端的覆盖情况。

    这是个**该被测试盯住的不变量**：真机词表加了实验，
    qiskit 这边如果没跟上，覆盖报告里就会少一条。
    """
    real = _experiment_configs()
    real_names = set(real.keys())
    qiskit_names = set(EXPERIMENT_REGISTRY.keys())
    return {
        "real_experiments": sorted(real_names),
        "qiskit_experiments": sorted(qiskit_names),
        "missing_in_qiskit": sorted(real_names - qiskit_names),
        "extra_in_qiskit": sorted(qiskit_names - real_names),
        "covered": len(real_names & qiskit_names),
        "total": len(real_names),
    }


# --------------------------------------------------------------------- 调度


def run_experiment(req: QiskitRequest) -> ExperimentOutcome:
    """按实验名执行。未知实验名抛 KeyError；qiskit 不可用抛 QiskitUnavailable。"""
    spec = EXPERIMENT_REGISTRY.get(req.experiment)
    if spec is None:
        raise KeyError(req.experiment)
    if not spec.impl:
        raise QiskitUnavailable(f"实验 {req.experiment} 没有绑定实现")

    module_name, _, func_name = spec.impl.partition(":")
    try:
        module = importlib.import_module(f"{__package__}.{module_name}")
    except ImportError as exc:
        raise QiskitUnavailable(
            f"qiskit 后端不可用（{exc}）。"
            "请在服务所用的 Python 环境里安装：pip install -r python/requirements-qiskit.txt"
        ) from exc

    builder = getattr(module, func_name, None)
    if builder is None:
        raise QiskitUnavailable(f"实验 {req.experiment} 的实现 {spec.impl} 不存在")
    outcome = builder(req)
    # 多比特（2026-11-19）：把这次实际用到的比特名记进结果，
    # 这样"哪个比特跑的"在 qiskit 结果里可查（以前只有单比特隐含在 code 里）。
    names = req.qubit_list
    if names:
        outcome.extra.setdefault("qubits", names)
        outcome.extra.setdefault("qubit", names[0])
    return outcome

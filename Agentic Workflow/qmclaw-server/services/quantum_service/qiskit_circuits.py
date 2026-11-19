"""
services/quantum_service/qiskit_circuits.py — Qiskit 线路构造与拟合实现

把 `config/experiment_configs.json` 里的**同一套实验词表**映射到 qiskit 线路。
词表与真机共用，这样上层（agent / workflow / 前端）不需要为 qiskit 另开一套命名。

**为什么要做"映射"而不是"直接跑线路"**

`sq.*` 是超导测控语义（扫频找 P1、T1 指数衰减、IQ 聚类），
qiskit 是门模型语义（h / cx / measure）。两者没有一一对应关系，
所以每个实验都要有一条明确的"物理等价物"：

- **exact**：qiskit 有原生等价物（Ramsey = H→phase→H、T1 = amplitude damping、
  SWAP = swap 门），线路本身就是那个物理过程。
- **model**：qiskit 没有原生等价物（VNA 扫频、腔 S21、DRAG 泄漏），
  用一个**显式声明的等效模型**换算成线路。曲线形态可信，
  但绝对刻度（GHz / µs）取决于传入的先验参数，**不等于真机标定值**。

这个区分会随每个实验的结果一起返回（`approximation` 字段），
调用方据此判断该数据能不能当真机值用。

**单位制**：qiskit 里 ħ=1，所有时间/频率都是无量纲的。
T1/T2 等有物理单位的量，靠入参里的先验（`t1_us` / `t2_us`）换算成 SI，
换算关系写在该实验的 `params` 描述里。

**分层**：本文件**依赖 qiskit**，`import` 它就等于要求环境里装了 qiskit。
目录/元数据在 `qiskit_experiments.py`（不依赖 qiskit），
由那边懒加载本文件 —— 这样没装 qiskit 的机器上服务照样起得来，
只是真正执行实验时才报"后端不可用"。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from qiskit import QuantumCircuit
from qiskit.quantum_info import Pauli, Statevector, random_clifford
from qiskit_aer import AerSimulator
from qiskit_aer.noise import (
    NoiseModel,
    ReadoutError,
    amplitude_damping_error,
    depolarizing_error,
    thermal_relaxation_error,
)

from services.quantum_service.qiskit_experiments import ExperimentOutcome, QiskitRequest

# 噪声门名：给"门级误差"实验（xeb / allxy / swap）挂退极化噪声用。
# **必须按比特数分开**：NoiseModel 拒绝把 1 比特错误挂到 2 比特指令上
# （NoiseError: '1 qubit QuantumError cannot be applied to 2 qubit instruction "cx"'）。
#
# 名单要覆盖 random_clifford().to_circuit() 实际展开出来的门
# （h / s / sdg / sx / sxdg / x / y / z …）。漏掉几个的话，
# 噪声只挂在少数几种门上，XEB 算出来的保真度会虚高到 0.99+，
# 看起来"跑通了"，其实是大部分门根本没加噪。
_NOISY_GATES_1Q = [
    "h", "s", "sdg", "t", "tdg", "sx", "sxdg", "x", "y", "z",
    "p", "rz", "rx", "ry", "u", "u1", "u2", "u3", "id",
]
_NOISY_GATES_2Q = ["cx", "swap", "cz", "echo"]


# --------------------------------------------------------------------- 底层工具


def _sv_z(circ: QuantumCircuit) -> float:
    """无噪声态矢量的 ⟨Z⟩（确定性，不消耗 shots）。"""
    sv = Statevector.from_instruction(circ)
    return float(np.real(sv.expectation_value(Pauli("Z"))))


def _sv_expect(circ: QuantumCircuit, pauli: str) -> float:
    """无噪声态矢量的 ⟨P⟩（实部）。"""
    sv = Statevector.from_instruction(circ)
    return float(np.real(sv.expectation_value(Pauli(pauli))))


def _aer_counts(
    circ: QuantumCircuit,
    shots: int,
    seed: int,
    noise_model: Optional[NoiseModel] = None,
) -> Dict[str, int]:
    """跑一次有 shots 的测量，返回 counts。"""
    sim = AerSimulator(noise_model=noise_model) if noise_model is not None else AerSimulator()
    res = sim.run(circ, shots=shots, seed_simulator=seed).result()
    return dict(res.get_counts(0))


def _p1(counts: Dict[str, int], shots: int) -> float:
    """P(|1⟩)（单比特 counts，键是 '0' / '1'）。"""
    return counts.get("1", 0) / float(shots) if shots else 0.0


def _qubit_bit(key: str, qubit_index: int) -> str:
    """从 counts 的键里取某个**量子比特**对应的位。

    qiskit 的 counts 串是**高位在左**（c_{n-1}…c_1 c_0）。
    经典比特 c_q 就是量子比特 q 的测量结果（`measure([0,1],[0,1])` 时 q↔c 一一对应），
    所以量子比特 q 对应串里从右往左数第 q 个字符。
    """
    bits = key.replace(" ", "")
    pos = len(bits) - 1 - qubit_index
    if 0 <= pos < len(bits):
        return bits[pos]
    return "0"


def _z_from_counts(counts: Dict[str, int], shots: int, qubit_index: int = 0) -> float:
    """从 counts 还原某个量子比特的 ⟨Z⟩。"""
    if not shots:
        return 0.0
    total = 0
    for key, count in counts.items():
        total += count * (1 if _qubit_bit(key, qubit_index) == "1" else -1)
    return total / float(shots)


def _fit_exp_decay(t: List[float], y: List[float]) -> Optional[float]:
    """拟合 y = exp(-t/T)，返回 T。点数不足或斜率非负时返回 None。"""
    pairs = [(tt, yy) for tt, yy in zip(t, y) if yy is not None and yy > 1e-9 and tt > 0]
    if len(pairs) < 3:
        return None
    ts = np.array([p[0] for p in pairs], dtype=float)
    ys = np.log(np.array([p[1] for p in pairs], dtype=float))
    slope, _ = np.polyfit(ts, ys, 1)
    if not np.isfinite(slope) or slope >= 0:
        return None
    return float(-1.0 / slope)


def _estimate_frequency(t: List[float], y: List[float]) -> float:
    """用 FFT 估一个初始频率（只用作拟合初值）。

    **不要用 `det_guess or 1e6` 这种写法**：det_guess=0.0 是 falsy，
    会被换成 1 MHz —— 而真实信号里根本没有 1 MHz 的成分，
    拟合就从完全错误的起点出发，最后收敛到一个纯属噪声的频率。
    """
    ts = np.asarray(t, dtype=float)
    ys = np.asarray(y, dtype=float)
    if ts.size < 4:
        return 0.0
    dt = float(np.median(np.diff(ts))) if ts.size > 1 else 1.0
    if dt <= 0:
        return 0.0
    centered = ys - ys.mean()
    spectrum = np.abs(np.fft.rfft(centered))
    freqs = np.fft.rfftfreq(centered.size, d=dt)
    if spectrum.size <= 1:
        return 0.0
    peak = int(np.argmax(spectrum[1:])) + 1
    return float(freqs[peak])


def _fit_damped_cosine(
    t: List[float],
    y: List[float],
    det_guess_hz: float = 0.0,
) -> Tuple[Optional[float], Optional[float], bool]:
    """拟合 y = A·exp(-t/T2)·cos(2π·f·t + φ)。

    返回 (T2, f, resolved)：
    - T2 / f 为 None 表示拟合失败
    - resolved=False 表示扫描窗口内不足半个周期，**频率不可分辨**，
      此时调用方应改用先验值（否则会报出一个纯噪声的频率）
    """
    try:
        from scipy.optimize import curve_fit
    except ImportError:
        return None, None, False

    ts = np.asarray(t, dtype=float)
    ys = np.asarray(y, dtype=float)
    if ts.size < 5:
        return None, None, False

    def model(tt, amp, tau, freq, phase):
        return amp * np.exp(-tt / max(tau, 1e-12)) * np.cos(2 * np.pi * freq * tt + phase)

    span = float(ts.max() - ts.min()) or 1.0
    # 初值：频率用 FFT 估；给定的 det_guess 只用来兜底
    freq0 = _estimate_frequency(ts, ys)
    if freq0 <= 0.0:
        freq0 = abs(float(det_guess_hz)) or span
    freq0 = min(max(freq0, 1e-9), span)
    p0 = [max(float(np.ptp(ys)), 1e-3), span, freq0, 0.0]
    try:
        popt, _ = curve_fit(
            model, ts, ys, p0=p0, maxfev=40000,
            bounds=(
                [0.0, 1e-12, -span, -10 * np.pi],
                [2.0, span * 20 + 1.0, span, 10 * np.pi],
            ),
        )
    except Exception:
        return None, None, False
    tau = float(popt[1])
    freq = float(popt[2])
    if not math.isfinite(tau) or tau <= 0:
        return None, None, False
    # 窗口内不足半个周期 → 频率不可分辨
    resolved = abs(freq) * span >= 0.5
    return tau, freq, resolved


def _extremum_center(x: List[float], y: List[float], mode: str = "max") -> Optional[float]:
    """求曲线极值位置（峰位/谷位），用相邻三点做抛物线插值细化。

    比二次多项式全局拟合稳得多 —— 谱学曲线是洛伦兹型，
    全局二次拟合会把整条曲线当成抛物线，峰位可能偏到窗口边缘。
    """
    if len(x) < 3 or len(x) != len(y):
        return None
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    idx = int(np.argmax(ys)) if mode == "max" else int(np.argmin(ys))
    if idx == 0 or idx == len(ys) - 1:
        return float(xs[idx])

    y0, y1, y2 = float(ys[idx - 1]), float(ys[idx]), float(ys[idx + 1])
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-18:
        return float(xs[idx])
    delta = 0.5 * (y0 - y2) / denom
    if not math.isfinite(delta) or abs(delta) > 1.0:
        return float(xs[idx])
    step = float(xs[idx + 1] - xs[idx])
    return float(xs[idx] + delta * step)


def _hellinger_fidelity(p: Dict[str, float], q: Dict[str, float]) -> float:
    """Bhattacharyya 系数的平方 —— XEB 用的那个保真度定义。"""
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    total = 0.0
    for key in keys:
        total += math.sqrt(max(p.get(key, 0.0), 0.0) * max(q.get(key, 0.0), 0.0))
    return float(min(max(total ** 2, 0.0), 1.0))


def _detuning_response(
    delta_hz: np.ndarray,
    rabi_rate_hz: float,
    p_max: float = 1.0,
) -> np.ndarray:
    """失谐驱动的跃迁概率 P_up(δ) —— 长脉冲极限（Landau-Zener）。

    P_up(δ) = Ω²/(Ω² + δ²) · p_max，Ω = 2π·rabi_rate_hz。

    选这个式子而不是有限脉宽的 sin²(Ω_eff·t/2) 形式，有两个理由：

    1. **无旁瓣**。有限脉宽公式在远离共振处会因 sin² 振荡出旁瓣，
       谱学图上就是一串假峰；洛伦兹线型才是扫描实验该有的样子。
    2. **标度自洽**。Ω 的时间单位必须和驱动时长匹配，否则整条曲线
       会被 sin² 的小角度近似压成一条平线（共振结构根本没出来）。
       这里 Ω 直接用 rabi_rate_hz 定义，HWHM 就是 rabi_rate_hz（Hz），
       与扫描窗口 df_hz 天然可比。
    """
    omega = 2.0 * np.pi * rabi_rate_hz
    delta = 2.0 * np.pi * np.asarray(delta_hz, dtype=float)
    denom = omega ** 2 + delta ** 2
    p = np.where(denom > 0, omega ** 2 / np.where(denom > 0, denom, 1.0), 0.0)
    return np.clip(p * p_max, 0.0, 1.0)


def _population_circuit(p_up: float) -> QuantumCircuit:
    """构造一个达到指定 P(|1⟩) 的单比特线路。

    R_y(β) 作用在 |0⟩ 上给出 P(|1⟩) = sin²(β/2)，取 β = 2·arcsin(√p) 即得。
    这是"等效态制备"：驱动失谐那一段是解析算的，态的制备与测量交给 qiskit。
    """
    p = float(min(max(p_up, 0.0), 1.0))
    beta = 2.0 * math.asin(math.sqrt(p))
    qc = QuantumCircuit(1)
    qc.ry(beta, 0)
    return qc


# --------------------------------------------------------------------- 各实验


def _build_t1(req: QiskitRequest) -> ExperimentOutcome:
    """T1 弛豫 —— amplitude damping 通道，**原生等价**。

    制备 |1⟩ → 施加衰减 γ = 1 - exp(-t/T1) → P(|1⟩) = exp(-t/T1)。
    线路就是那个物理过程，所以是 exact。
    """
    t1_sec = req.pf("t1_us", 50.0) * 1e-6
    points = req.pi("points", 31)
    t_max = req.pf("t_max_us", 5.0 * t1_sec * 1e6) * 1e-6

    times = list(np.linspace(0.0, t_max, points))
    xs: List[float] = []
    ys: List[float] = []

    for t in times:
        gamma = 1.0 - math.exp(-t / t1_sec) if t1_sec > 0 else 0.0
        nm = NoiseModel()
        nm.add_all_qubit_quantum_error(amplitude_damping_error(gamma, 0), ["id"])

        qc = QuantumCircuit(1, 1)
        qc.x(0)
        qc.id(0)
        qc.measure(0, 0)

        counts = _aer_counts(qc, req.shots, req.seed, nm)
        xs.append(float(t))
        ys.append(_p1(counts, req.shots))

    fitted = _fit_exp_decay(xs, ys)
    metrics: Dict[str, Any] = {"T1": fitted if fitted is not None else t1_sec}
    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="Delay (s)",
        ylabel="P(|1>)",
        metrics=metrics,
        extra={
            "T1_prior_us": t1_sec * 1e6,
            "T1_fit_us": (fitted * 1e6) if fitted is not None else None,
            "unit_note": "T1 由入参 t1_us 先验定标；T1_fit 为本次 shots 拟合值。",
        },
    )


def _build_ramsey(req: QiskitRequest) -> ExperimentOutcome:
    """Ramsey —— H → phase(ωt) → H，叠加相位 + phase damping。**原生等价**。"""
    t2_sec = req.pf("t2_us", 20.0) * 1e-6
    t1_relax = req.pf("t1_us", 3.0 * t2_sec * 1e6) * 1e-6
    det_hz = req.pf("detuning_hz", 0.0)
    points = req.pi("points", 41)
    t_max = req.pf("t_max_us", 5.0 * t2_sec * 1e6) * 1e-6

    times = list(np.linspace(0.0, t_max, points))
    xs: List[float] = []
    ys: List[float] = []

    for t in times:
        # 用 thermal_relaxation_error 而不是 phase_damping_error：
        # 前者是"自由演化 t 秒"的热弛豫通道，⟨Z⟩ 包络 = e^{-t/T2}（实测 19.948 vs 理想 20.000 µs）；
        # 后者的两种参数化都给不出正确的包络（实测拟合出 40 µs / 391 µs）。
        nm = NoiseModel()
        nm.add_all_qubit_quantum_error(
            thermal_relaxation_error(t1_relax, t2_sec, t, 0), ["id"]
        )

        qc = QuantumCircuit(1, 1)
        qc.h(0)
        qc.id(0)
        qc.p(2.0 * np.pi * det_hz * t, 0)
        qc.h(0)
        qc.measure(0, 0)

        counts = _aer_counts(qc, req.shots, req.seed, nm)
        p0 = counts.get("0", 0) / float(req.shots) if req.shots else 0.0
        xs.append(float(t))
        ys.append(2.0 * p0 - 1.0)

    tau, freq, resolved = _fit_damped_cosine(xs, ys, det_hz)
    # 频率不可分辨时（窗口内不足半个周期）**回退到先验**，
    # 宁可给 0 也不要给一个纯噪声拟合出来的几 MHz。
    detuning_value = freq if (resolved and freq is not None) else det_hz
    metrics: Dict[str, Any] = {
        "T2": tau if tau is not None else t2_sec,
        "detuning": detuning_value,
    }
    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="Delay (s)",
        ylabel="<Z>",
        metrics=metrics,
        extra={
            "T2_prior_us": t2_sec * 1e6,
            "T2_fit_us": (tau * 1e6) if tau is not None else None,
            "detuning_prior_hz": det_hz,
            "detuning_resolved": bool(resolved),
            "unit_note": "T2 由入参 t2_us 先验定标；detuning 单位 Hz（相对驱动频率）。",
        },
    )


def _build_piamp(req: QiskitRequest) -> ExperimentOutcome:
    """π 脉冲幅度标定 —— Rabi 振荡扫描。**原生等价**（振幅→转角用饱和模型）。

    转角与驱动幅度的关系用 tanh 饱和模型 θ(a) = θ_sat·tanh(k·a)，
    真机上这层非线性来自功放/DRAG；这里显式给出参数，可被替换。
    """
    amp_max = req.pf("amp_max", 1.0)
    points = req.pi("points", 41)
    theta_sat = req.pf("theta_sat", 2.0 * math.pi)
    sat_k = req.pf("sat_k", 2.0)

    amps = list(np.linspace(0.0, amp_max, points))
    xs: List[float] = []
    ys: List[float] = []

    for amp in amps:
        theta = theta_sat * math.tanh(sat_k * amp)
        qc = QuantumCircuit(1)
        qc.ry(theta, 0)
        xs.append(float(amp))
        ys.append(_sv_z(qc))

    # π 脉冲 = 转角到 π 的位置，也就是 ⟨Z⟩ 的**最低点**（完全反转）。
    # 注意不是"零点"：cos(θ)=0 在 θ=π/2 就成立，那是半π脉冲。
    pi_amp: Optional[float] = None
    idx = int(np.argmin(ys)) if ys else -1
    if idx >= 0:
        if 0 < idx < len(ys) - 1:
            y0, y1, y2 = ys[idx - 1], ys[idx], ys[idx + 1]
            denom = y0 - 2.0 * y1 + y2
            if abs(denom) > 1e-18:
                delta = 0.5 * (y0 - y2) / denom
                if abs(delta) <= 1.0:
                    step = xs[idx + 1] - xs[idx]
                    pi_amp = float(xs[idx] + delta * step)
                else:
                    pi_amp = float(xs[idx])
            else:
                pi_amp = float(xs[idx])
        else:
            pi_amp = float(xs[idx])

    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="Drive amplitude (a.u.)",
        ylabel="<Z>",
        metrics={"pi_amplitude": pi_amp if pi_amp is not None else amp_max / 2.0},
        extra={
            "theta_sat": theta_sat,
            "sat_k": sat_k,
            "unit_note": "amplitude 为归一化驱动幅度（无量纲）；pi_amplitude 为首次过零处。",
        },
    )


def _build_spectroscopy(req: QiskitRequest) -> ExperimentOutcome:
    """频率扫描找共振 —— **等效模型**（qiskit 没有 VNA / 驱动频率）。

    扫描失谐 δ，测跃迁概率 P_up(δ)，峰值处即共振。
    """
    span_hz = req.pf("df_hz", 5e6)
    points = req.pi("points", 51)
    rabi_rate_hz = req.pf("rabi_rate_hz", 1e6)
    p_max = req.pf("p_max", 1.0)

    deltas = list(np.linspace(-span_hz, span_hz, points))
    p_up = _detuning_response(np.array(deltas), rabi_rate_hz, p_max)

    xs: List[float] = []
    ys: List[float] = []
    for delta, prob in zip(deltas, p_up):
        qc = _population_circuit(float(prob))
        z = _sv_z(qc)
        # 画 P(|1⟩) 而不是 ⟨Z⟩：谱学曲线习惯上是**峰**在共振处，
        # 而 ⟨Z⟩ 在共振处是谷（p_up 越大 ⟨Z⟩ 越小）。
        xs.append(float(delta))
        ys.append((1.0 - z) / 2.0)

    center = _extremum_center(xs, ys, mode="max")
    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="Detuning (Hz)",
        ylabel="P(|1>)",
        metrics={"f10": center if center is not None else 0.0},
        extra={
            "f10_unit": "Hz（相对驱动频率的失谐零点，**不是**绝对比特频率）",
            "rabi_rate_hz": rabi_rate_hz,
            "p_max": p_max,
            "unit_note": "长脉冲 Landau-Zener 等效模型；线宽 = rabi_rate_hz，绝对刻度依赖入参。",
        },
    )


def _build_pulsed_spec(req: QiskitRequest) -> ExperimentOutcome:
    """脉冲谱学 —— spectroscopy + π 泵浦脉冲。**等效模型**。

    与 spectroscopy 的区别：先做一次 π 泵浦把比特打到 |1⟩，再扫频探测。
    """
    span_hz = req.pf("df_hz", 5e6)
    points = req.pi("points", 51)
    rabi_rate_hz = req.pf("rabi_rate_hz", 1e6)
    p_max = req.pf("p_max", 1.0)

    deltas = list(np.linspace(-span_hz, span_hz, points))
    p_up = _detuning_response(np.array(deltas), rabi_rate_hz, p_max)

    xs: List[float] = []
    ys: List[float] = []
    for delta, prob in zip(deltas, p_up):
        qc = QuantumCircuit(1)
        qc.x(0)                       # π 泵浦：制备 |1⟩
        probe = _population_circuit(float(prob))
        qc.compose(probe, inplace=True)
        # 制备在 |1⟩，所以观测量是 P(|0⟩)（被驱动打下去的概率）= (1+⟨Z⟩)/2。
        # 用 (1-⟨Z⟩)/2 会画成谷，峰位直接跑到扫描窗口边缘去。
        z = _sv_z(qc)
        xs.append(float(delta))
        ys.append((1.0 + z) / 2.0)

    center = _extremum_center(xs, ys, mode="max")
    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="Detuning (Hz)",
        ylabel="P(|0>)",
        metrics={"qubit_frequency": center if center is not None else 0.0},
        extra={
            "f10_unit": "Hz（相对驱动频率的失谐零点）",
            "rabi_rate_hz": rabi_rate_hz,
            "unit_note": "带 π 泵浦的长脉冲 Landau-Zener 等效模型。",
        },
    )


def _build_s21(req: QiskitRequest) -> ExperimentOutcome:
    """腔透射 S21 —— **等效模型**（qiskit 没有谐振腔）。

    用比特跃迁概率作为腔透射的代理：共振处透射下凹。
    """
    span_hz = req.pf("df_hz", 5e6)
    points = req.pi("points", 51)
    rabi_rate_hz = req.pf("rabi_rate_hz", 1e6)
    p_max = req.pf("p_max", 1.0)
    depth = req.pf("depth", 0.8)

    deltas = list(np.linspace(-span_hz, span_hz, points))
    p_up = _detuning_response(np.array(deltas), rabi_rate_hz, p_max)

    xs: List[float] = []
    ys: List[float] = []
    for delta, prob in zip(deltas, p_up):
        qc = _population_circuit(float(prob))
        # 腔透射代理：1 - depth·P_up（共振处下凹）
        xs.append(float(delta))
        ys.append(1.0 - depth * float(prob))

    center = _extremum_center(xs, ys, mode="min")
    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="Detuning (Hz)",
        ylabel="S21 (proxy)",
        metrics={"fread": center if center is not None else 0.0},
        extra={
            "fread_unit": "Hz（相对驱动频率的失谐零点）",
            "depth": depth,
            "rabi_rate_hz": rabi_rate_hz,
            "unit_note": "腔透射由比特跃迁概率代理，非真实腔模型。",
        },
    )


def _build_s21_dis(req: QiskitRequest) -> ExperimentOutcome:
    """色散位移 —— **等效模型**。

    比特处于 |0⟩ / |1⟩ 时腔的共振中心相差 χ，两条曲线的峰位差即色散位移。
    """
    span_hz = req.pf("df_hz", 5e6)
    points = req.pi("points", 51)
    rabi_rate_hz = req.pf("rabi_rate_hz", 1e6)
    p_max = req.pf("p_max", 1.0)
    chi_hz = req.pf("chi_hz", 2e6)
    depth = req.pf("depth", 0.8)

    deltas = list(np.linspace(-span_hz, span_hz, points))

    curves: Dict[str, List[float]] = {}
    centers: Dict[str, float] = {}
    for state, shift in (("0", -chi_hz / 2.0), ("1", +chi_hz / 2.0)):
        shifted = _detuning_response(np.array(deltas) - shift, rabi_rate_hz, p_max)
        y_state: List[float] = []
        for prob in shifted:
            qc = _population_circuit(float(prob))
            y_state.append(1.0 - depth * float(prob))
        curves[state] = y_state
        # 腔透射在共振处是**谷**，取谷位
        center = _extremum_center([float(d) for d in deltas], y_state, mode="min")
        centers[state] = center if center is not None else shift

    shift_value = centers["1"] - centers["0"]
    # 图上画 |1⟩ 态的腔曲线（|0⟩ 态那条放在 extra 里）
    ys = curves["1"]

    return ExperimentOutcome(
        x=[float(d) for d in deltas],
        y=ys,
        xlabel="Detuning (Hz)",
        ylabel="S21 (proxy)",
        metrics={"dispersive_shift": shift_value},
        extra={
            "curves": curves,
            "centers": centers,
            "chi_prior_hz": chi_hz,
            "unit_note": "两态腔曲线的峰位差；曲线本身为代理量。",
        },
    )


def _build_iqraw(req: QiskitRequest) -> ExperimentOutcome:
    """IQ 原始数据 —— 单发读出的 I/Q 点云。**等效模型**。

    每个样品先制备 |0⟩ 或 |1⟩，加一个随机的读出相位（真实系统里的相位抖动），
    再绕 X 轴转 45°，此时 ⟨σx⟩ / ⟨σy⟩ 就是 IQ 平面的两个坐标。
    """
    samples = req.pi("samples", 300)
    p_readout = min(max(req.pf("p_readout", 0.03), 0.0), 0.5)
    phase_sigma = req.pf("phase_sigma", 0.15)
    seed_offset = req.pi("seed_offset", 0)

    rng = np.random.default_rng(req.seed + seed_offset)

    nm = NoiseModel()
    if p_readout > 0:
        nm.add_all_qubit_readout_error(
            ReadoutError([[1.0 - p_readout, p_readout], [p_readout, 1.0 - p_readout]])
        )

    points: Dict[str, List[Tuple[float, float]]] = {"0": [], "1": []}
    for state in (0, 1):
        for _ in range(samples):
            phase = float(rng.normal(0.0, phase_sigma))
            qc = QuantumCircuit(1, 1)
            if state:
                qc.x(0)
            qc.rz(phase, 0)
            qc.ry(math.pi / 4.0, 0)      # 转到 x-y 测量基
            qc.measure(0, 0)

            counts = _aer_counts(qc, max(req.shots // 20, 200), req.seed, nm)
            bit = "1" if counts.get("1", 0) > counts.get("0", 0) else "0"
            # 用该次制备的真实态算 IQ 坐标（读出比特只用来挑"这一次被判成什么"）
            qc_true = QuantumCircuit(1)
            if state:
                qc_true.x(0)
            qc_true.rz(phase, 0)
            qc_true.ry(math.pi / 4.0, 0)
            i_val = _sv_expect(qc_true, "X")
            q_val = _sv_expect(qc_true, "Y")
            # 读出误判 → 记到另一个簇
            if bit != str(state):
                i_val, q_val = -i_val, -q_val
            points[str(state)].append((i_val, q_val))

    xs: List[float] = []
    ys: List[float] = []
    for state in ("0", "1"):
        for i_val, q_val in points[state]:
            xs.append(float(i_val))
            ys.append(float(q_val))

    f0 = np.array(points["0"], dtype=float)
    f1 = np.array(points["1"], dtype=float)
    c0 = f0.mean(axis=0) if f0.size else np.zeros(2)
    c1 = f1.mean(axis=0) if f1.size else np.zeros(2)
    separation = float(np.linalg.norm(c0 - c1))
    spread = float(np.std(np.concatenate([f0, f1]), axis=0).mean()) if xs else 0.0
    snr = separation / spread if spread > 0 else 0.0

    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="I",
        ylabel="Q",
        plot_type="scatter",
        metrics={
            "SNR": snr,
            "F0": [float(c0[0]), float(c0[1])],
            "F1": [float(c1[0]), float(c1[1])],
            "separation": separation,
        },
        extra={
            "p_readout": p_readout,
            "samples_per_state": samples,
            "unit_note": "IQ 坐标为归一化量（|⟨σx⟩|, |⟨σy⟩|），非 volts。",
        },
    )


def _build_single_shot(req: QiskitRequest) -> ExperimentOutcome:
    """单发读出保真度 —— 加读出翻转通道。**原生等价**。"""
    p_readout = min(max(req.pf("p_readout", 0.03), 0.0), 0.5)

    nm = NoiseModel()
    nm.add_all_qubit_readout_error(
        ReadoutError([[1.0 - p_readout, p_readout], [p_readout, 1.0 - p_readout]])
    )

    fids: Dict[str, float] = {}
    for state in (0, 1):
        qc = QuantumCircuit(1, 1)
        if state:
            qc.x(0)
        qc.measure(0, 0)
        counts = _aer_counts(qc, req.shots, req.seed, nm)
        correct = counts.get(str(state), 0)
        fids[str(state)] = correct / float(req.shots) if req.shots else 0.0

    fidelity = (fids["0"] + fids["1"]) / 2.0
    return ExperimentOutcome(
        x=[0, 1],
        y=[fids["0"], fids["1"]],
        xlabel="Prepared state",
        ylabel="Readout fidelity",
        plot_type="bar",
        metrics={"readout_fidelity": fidelity},
        extra={"f0": fids["0"], "f1": fids["1"], "p_readout": p_readout},
    )


def _build_swap(req: QiskitRequest) -> ExperimentOutcome:
    """SWAP 门表征 —— **原生等价**（两比特 swap 门 + Bell 基测量）。

    多比特（2026-11-19）：`req.qubit_list` 的**顺序**就是线路里两个物理比特的
    顺序 —— names[0] → 线路 qubit 0，names[1] → 线路 qubit 1。
    只给一个名字时第二个用默认 `q1`（与旧行为一致）。
    """
    error_rate = req.pf("error_rate", 0.01)
    names = req.qubit_list
    qa = names[0] if names else "q0"
    qb = names[1] if len(names) > 1 else "q1"

    nm = NoiseModel()
    if error_rate > 0:
        nm.add_all_qubit_quantum_error(depolarizing_error(error_rate, 2), ["cx", "swap"])

    qc = QuantumCircuit(2, 2)
    qc.x(1)            # 制备 |10⟩（线路 qubit 1 = 名字 qb）
    qc.swap(0, 1)      # 期望交换成 |01⟩
    qc.measure([0, 1], [0, 1])

    counts = _aer_counts(qc, req.shots, req.seed, nm)
    # qiskit counts 键是 little-endian：'01' 表示 q0=1, q1=0
    total = float(sum(counts.values())) or 1.0
    fidelity = counts.get("01", 0) / total

    return ExperimentOutcome(
        x=[0, 1],
        y=[1.0 - fidelity, fidelity],
        xlabel=f"Outcome (|10> |01>)  [{qa} | {qb}]",
        ylabel="Probability",
        plot_type="bar",
        metrics={"swap_fidelity": fidelity},
        extra={"counts": counts, "error_rate": error_rate,
               "qubits": [qa, qb], "qubit_a": qa, "qubit_b": qb},
    )


def _build_xeb(req: QiskitRequest) -> ExperimentOutcome:
    """交叉熵基准 —— 随机 Clifford 序列 vs 理想分布。**原生等价**。

    XEB 的要害是**保真度随线路深度的衰减**，不是单条线路的保真度：

        F(d) ≈ A · α^d        →    每周期错误率 ε = 1 - α

    把不同深度的序列混在一起平均，会得到一个"看起来很漂亮的平均值"，
    但它既不随深度变化，`error_per_cycle` 也就不随注入的噪声变化 ——
    指标在物理上是空的。这里按深度分层测 F(d)，再对 log F(d) 做直线拟合。

    注意 Hellinger/Bhattacharyya 保真度对小偏差是**二次**敏感的：
    概率差 2.6% 只对应保真度差 0.2%。所以单看一条线路的 F 很难判断噪声，
    必须靠深度的累积效应。
    """
    cycles = req.pi("cycles", 8)
    seqs_per_cycle = req.pi("sequences", 12)
    error_rate = req.pf("error_rate", 0.01)

    nm = NoiseModel()
    if error_rate > 0:
        nm.add_all_qubit_quantum_error(depolarizing_error(error_rate, 1), _NOISY_GATES_1Q)

    sim = AerSimulator(noise_model=nm) if error_rate > 0 else AerSimulator()

    depth_fidelity: List[float] = []
    per_sequence: List[List[float]] = []
    counter = 0

    for depth in range(1, cycles + 1):
        fids: List[float] = []
        for _ in range(seqs_per_cycle):
            # 深度为 depth 的随机 Clifford 线路：每层一个随机 Clifford
            circ = QuantumCircuit(1)
            for layer in range(depth):
                cliff = random_clifford(1, seed=int(req.seed + counter * 977 + layer))
                circ.compose(cliff.to_circuit(), inplace=True)
            counter += 1

            # 随机测量基：先 rz 再 ry（**不含 measure**）
            rng = np.random.default_rng(req.seed + counter)
            theta = float(rng.uniform(0, 2 * np.pi))
            phi = float(rng.uniform(0, np.pi))
            basis_rot = QuantumCircuit(1)
            basis_rot.rz(theta, 0)
            basis_rot.ry(phi, 0)

            # 理想分布：态矢量**不能带 measure**（Statevector 拒绝经典比特指令）
            ideal_circ = circ.compose(basis_rot, inplace=False)
            ideal_probs = Statevector.from_instruction(ideal_circ).probabilities_dict()
            ideal_norm = {k: float(v) for k, v in ideal_probs.items()}

            # 实测分布：同一个旋转 + 测量
            meas = QuantumCircuit(1, 1)
            meas.measure(0, 0)
            noisy_circ = circ.compose(basis_rot.compose(meas, inplace=False), inplace=False)
            res = sim.run(noisy_circ, shots=req.shots, seed_simulator=req.seed + counter).result()
            raw = dict(res.get_counts(0))
            total = float(sum(raw.values())) or 1.0
            empirical = {k: v / total for k, v in raw.items()}

            fids.append(_hellinger_fidelity(empirical, ideal_norm))

        per_sequence.append(fids)
        depth_fidelity.append(float(np.mean(fids)) if fids else 0.0)

    # F(d) = A·α^d  →  log F(d) = log A + d·log α
    alpha = 1.0
    amplitude = 1.0
    positive = [(d + 1, f) for d, f in enumerate(depth_fidelity) if f > 0]
    if len(positive) >= 2:
        ds = np.array([p[0] for p in positive], dtype=float)
        ls = np.log(np.array([p[1] for p in positive], dtype=float))
        slope, intercept = np.polyfit(ds, ls, 1)
        alpha = float(np.exp(slope))
        amplitude = float(np.exp(intercept))
    elif positive:
        alpha = float(positive[0][1])
        amplitude = 1.0

    alpha = min(max(alpha, 0.0), 1.0)
    error_per_cycle = 1.0 - alpha
    gate_fidelity = depth_fidelity[-1] if depth_fidelity else 0.0

    return ExperimentOutcome(
        x=list(range(1, cycles + 1)),
        y=depth_fidelity,
        xlabel="Circuit depth",
        ylabel="Mean fidelity",
        metrics={"gate_fidelity": gate_fidelity, "error_per_cycle": error_per_cycle},
        extra={
            "cycles": cycles,
            "sequences_per_cycle": seqs_per_cycle,
            "error_rate": error_rate,
            "alpha_per_cycle": alpha,
            "amplitude": amplitude,
            "per_sequence_fidelity": per_sequence,
            "unit_note": "F(d)=A·α^d 拟合；error_per_cycle = 1-α（每层 Clifford 的错误率）。",
        },
    )


def _build_allxy(req: QiskitRequest) -> ExperimentOutcome:
    """AllXY —— 门误差组合扫描。**等效模型**（用退极化噪声代表门误差）。

    经典 AllXY 序列（脉冲串）与各自的理想期望值；实测值与理想值的偏离即误差。
    """
    error_rate = req.pf("error_rate", 0.02)

    # 经典 AllXY 脉冲串（idle 用 I 表示）。
    # 理想值**不硬编码**，而是用无噪声线路现算 —— 硬编码那张表最容易在
    # 序列顺序约定上出错，错了会让 fidelity 看起来莫名其妙地低。
    sequences: List[str] = [
        "XX", "XY", "YX", "YY", "X", "Y", "XYX", "YXY",
        "XXI", "YYI", "XYI", "YXI",
    ]

    nm = NoiseModel()
    if error_rate > 0:
        nm.add_all_qubit_quantum_error(depolarizing_error(error_rate, 1), _NOISY_GATES_1Q)

    labels: List[str] = []
    measured: List[float] = []
    ideals: List[float] = []

    for seq in sequences:
        ideal_qc = QuantumCircuit(1)
        noisy_qc = QuantumCircuit(1, 1)
        for ch in seq:
            for circ in (ideal_qc, noisy_qc):
                if ch == "X":
                    circ.x(0)
                elif ch == "Y":
                    circ.y(0)
                elif ch == "I":
                    circ.id(0)

        ideal = _sv_z(ideal_qc)
        noisy_qc.measure(0, 0)

        counts = _aer_counts(noisy_qc, req.shots, req.seed, nm)
        p0 = counts.get("0", 0) / float(req.shots) if req.shots else 0.0
        labels.append(seq)
        measured.append(2.0 * p0 - 1.0)
        ideals.append(ideal)

    infidelities = [abs(m - i) / 2.0 for m, i in zip(measured, ideals)]
    average_fidelity = 1.0 - float(np.mean(infidelities)) if infidelities else 0.0
    leak_terms = [
        (1.0 - abs(m)) / 2.0 for m, i in zip(measured, ideals) if abs(i) > 0.5
    ]
    leakage = float(np.mean(leak_terms)) if leak_terms else 0.0

    return ExperimentOutcome(
        x=list(range(len(labels))),
        y=measured,
        xlabel="Sequence",
        ylabel="<Z>",
        metrics={"average_fidelity": average_fidelity, "leakage": leakage},
        extra={
            "sequences": labels,
            "ideal": ideals,
            "error_rate": error_rate,
            "unit_note": "门误差用退极化噪声代理，非真实 AllXY 脉冲整形。",
        },
    )


def _build_drag_calibrate(req: QiskitRequest) -> ExperimentOutcome:
    """DRAG 系数标定 —— **等效模型**（qiskit 无三能级，用"泄漏旗标"代理）。

    两个比特：q0 = 计算比特，q1 = 泄漏旗标。
    - 泄漏概率 p_leak(α) = k·(α − α_opt)²
    - 相位误差 φ(α) = c·α（DRAG 的正交分量带来的相干误差）
    扫 α，取保真度最高处作为 optimal_drag。

    多比特（2026-11-19）：`req.qubit_list[0]` 是计算比特，`[1]` 是泄漏旗标。
    """
    alpha_min = req.pf("alpha_min", -1.0)
    alpha_max = req.pf("alpha_max", 1.0)
    points = req.pi("points", 41)
    alpha_opt = req.pf("alpha_opt", 0.5)
    leak_k = req.pf("leak_k", 0.08)
    phase_c = req.pf("phase_c", 0.35)
    names = req.qubit_list
    qa = names[0] if names else "q0"
    qb = names[1] if len(names) > 1 else "q1"

    alphas = list(np.linspace(alpha_min, alpha_max, points))
    xs: List[float] = []
    ys: List[float] = []
    leaks: List[float] = []

    for alpha in alphas:
        p_leak = min(leak_k * (alpha - alpha_opt) ** 2, 0.95)
        phase = phase_c * alpha

        qc = QuantumCircuit(2, 2)
        qc.x(0)                                    # π 脉冲
        qc.rz(phase, 0)                            # 相干相位误差
        beta = 2.0 * math.asin(math.sqrt(p_leak))   # 泄漏旗标
        qc.ry(beta, 1)
        qc.measure([0, 1], [0, 1])

        counts = _aer_counts(qc, req.shots, req.seed, None)
        total = float(sum(counts.values())) or 1.0
        # 泄漏旗标是**量子比特 1**（counts 串里高位那一位），计算比特是 q0
        leak = sum(c for k, c in counts.items() if _qubit_bit(k, 1) == "1") / total
        z0 = _z_from_counts(counts, int(total), qubit_index=0)

        fidelity = (1.0 - leak) * (1.0 + z0) / 2.0
        xs.append(float(alpha))
        ys.append(float(fidelity))
        leaks.append(float(leak))

    best = int(np.argmax(ys)) if ys else 0
    return ExperimentOutcome(
        x=xs,
        y=ys,
        xlabel="DRAG alpha",
        ylabel="Fidelity (surrogate)",
        metrics={
            "optimal_drag": float(xs[best]) if xs else 0.0,
            "fidelity_at_opt": float(ys[best]) if ys else 0.0,
            "leakage_at_opt": float(leaks[best]) if leaks else 0.0,
        },
        extra={
            "alpha_opt_prior": alpha_opt,
            "leak_k": leak_k,
            "phase_c": phase_c,
            "leakage": leaks,
            "qubits": [qa, qb],
            "qubit_compute": qa,
            "qubit_leak_flag": qb,
            "unit_note": "qiskit 无三能级支持，泄漏用第二个比特的旗标代理。",
        },
    )



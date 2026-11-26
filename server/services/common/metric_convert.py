"""运行指标 → 状态机证据键的统一换算。

为什么要有这一层
----------------
一次测控运行回来的是「厂商/模拟后端各自习惯的键名」（``T1`` 秒、``separation``、
``SNR``、``gate_fidelity`` …），而状态机只认一小撮证据键（``t1_us``、``contrast``、
``xeb_gate_fidelity`` …）。这个换算以前散落在演示脚本、循环脚本、workflow 节点里，
每处口径略有不同 —— 同一份数据在两处能得出两个结论。

现在只有这一份实现：

- 微服务 ``POST /metrics/convert`` 直接调它；
- ``server/tools/demo_control_console.py``、``measurement_loop.py`` 复用它；
- 技能文档只描述物理口径，不再抄公式。

约定
----
1. **不改入参**，返回新的 ``(evidence, notes)``；``notes`` 逐条写「原值 → 证据值（口径）」，
   评审能顺着核对，不做隐式换算。
2. **缺就是缺**：换算不出来的键不出现，绝不用默认值补齐（补值属于剧本层的事）。
3. 状态机侧的白名单是 ``services/common/qubit_state.py`` 的 ``EVIDENCE_SOURCES``
   （另有 pulse / tunable scope 的 ``drag_delta``、``single_qubit_fidelity``、``zpa_curve``、
   ``t1_by_zpa``，由 state_machine.json 的 scope 声明）。本模块只负责数值口径，
   不判断「这个键属于哪个 scope」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 0..1 归一化时用到的经验口径（演示/模拟后端专用，真机优先走直接指标）
CONTRAST_FROM_SEPARATION = 2.0   # separation 定义成「两峰间距 / σ」，除以 2 折成 0..1
CONTRAST_FROM_SNR = 20.0         # SNR 20 视作满分
SECOND_TO_MICRO = 1e6

#: 直接透传的键（同名同义，值只要可转 float）
PASSTHROUGH_NUMERIC: Tuple[str, ...] = (
    # 已经是证据键的同名指标（拟合器与部分后端直接给这些名字）
    "contrast",
    "dmu_sig",
    "t1_us",
    "t2_star_us",
    "t2_echo_us",
    "xeb_p",
    "xeb_gate_fidelity",
    "drag_delta",
    "single_qubit_fidelity",
    "cphase",
    "cz_leakage",
    "cz_gate_fidelity",
    "zpa_curve",
    "t1_by_zpa",
    # 曲线类可能以点数组形式给出，_num 失败时下面的 bool/数值分支会跳过
)

#: 需要换单位的键：证据键 → (源键候选, 乘数, 说明)
UNIT_CONVERSIONS: Tuple[Tuple[str, Tuple[str, ...], float, str], ...] = (
    ("t1_us", ("T1_us",), 1.0, "直接取自 T1_us"),
    ("t1_us", ("T1",), SECOND_TO_MICRO, "秒 ×1e6"),
    ("t2_star_us", ("T2_us", "T2_star_us"), 1.0, "直接取自 T2_us"),
    ("t2_star_us", ("T2", "T2_star"), SECOND_TO_MICRO, "秒 ×1e6"),
    ("t2_echo_us", ("T2_echo_us",), 1.0, "直接取自 T2_echo_us"),
    ("t2_echo_us", ("T2_echo",), SECOND_TO_MICRO, "秒 ×1e6"),
)

#: 别名：源键 → 证据键
ALIASES: Tuple[Tuple[str, str], ...] = (
    ("gate_fidelity", "xeb_gate_fidelity"),
    ("p_xeb", "xeb_p"),
    ("xeb_gate_fid", "xeb_gate_fidelity"),
    ("f10_hz", "freq_locked"),
    ("f10", "freq_locked"),
    ("pi_amplitude", "amp_calibrated"),
)


#: 语义是布尔量的证据键（其余键收到 bool 视为噪声）
FLAG_EVIDENCE: Tuple[str, ...] = ("freq_locked", "amp_calibrated")


def _num(value: Any) -> Optional[float]:
    """把指标值转成 float；bool 不算数值（``True`` 不是一伏特的幅度）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _round(value: float) -> float:
    return round(float(value), 6)


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def to_evidence(metrics: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """把一次运行的 ``metrics`` 换算成状态机证据键。

    返回 ``(evidence, notes)``；``evidence`` 只含换算成功的键。
    """
    metrics = dict(metrics or {})
    evidence: Dict[str, Any] = {}
    notes: List[str] = []

    # 1) 同名同义，直接透传
    for key in PASSTHROUGH_NUMERIC:
        value = metrics.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            # bool 只在布尔型证据上成立；数值型证据收到 True 一律当噪声丢掉
            if key in FLAG_EVIDENCE:
                evidence[key] = value
                notes.append(f"{key}={value}（直接取自 metrics.{key}）")
            continue
        number = _num(value)
        if number is not None:
            evidence[key] = _round(number)
            notes.append(f"{key}={number:g}（直接取自 metrics.{key}）")

    # 2) contrast：真有就用真的，没有才把 separation / SNR 折成近似
    if "contrast" not in evidence:
        separation = _num(metrics.get("separation"))
        snr = _num(metrics.get("SNR"))
        if separation is not None:
            value = separation if separation <= 1.0 else min(1.0, separation / CONTRAST_FROM_SEPARATION)
            evidence["contrast"] = _round(value)
            notes.append(f"separation={separation:g} → contrast={value:.4f}（separation/2 归一化）")
        elif snr is not None:
            value = _clip01(snr / CONTRAST_FROM_SNR)
            evidence["contrast"] = _round(value)
            notes.append(f"SNR={snr:g} → contrast={value:.4f}（SNR/20 归一化，仅演示口径）")

    # 3) 带单位的键（T1 / T2 秒 → 微秒）
    for target, sources, factor, why in UNIT_CONVERSIONS:
        if target in evidence:
            continue
        for source in sources:
            raw = _num(metrics.get(source))
            if raw is None:
                continue
            value = raw * factor
            evidence[target] = _round(value)
            notes.append(f"{source}={raw:g} → {target}={value:.6g}（{why}）")
            break

    # 4) 别名族（xeb 的 p / gate_fidelity、f10 → freq_locked）
    for source, target in ALIASES:
        if target in evidence or source not in metrics:
            continue
        if target == "freq_locked":
            f10 = _num(metrics.get(source))
            if f10:
                evidence["freq_locked"] = True
                notes.append(f"{source}={f10:g} 已找到 → freq_locked=True（扫频成功即锁定频率）")
            elif "f10" in metrics or source in metrics:
                evidence["freq_locked"] = True
                notes.append(
                    f"{source}=0（模拟后端不返回峰位）：按「扫频已执行完成」记 freq_locked=True；真机需 f10>0"
                )
            continue
        if target == "amp_calibrated":
            amplitude = _num(metrics.get(source))
            if amplitude:
                evidence["amp_calibrated"] = True
                notes.append(f"{source}={amplitude:g} 已找到 → amp_calibrated=True（π 脉冲幅度已定）")
            continue
        raw = _num(metrics.get(source))
        if raw is not None:
            evidence[target] = _round(raw)
            notes.append(f"{source}={raw:g} → {target}")

    # 5) freq_locked 直接给布尔量时优先采信
    if "freq_locked" in metrics and not isinstance(metrics.get("freq_locked"), bool):
        evidence["freq_locked"] = bool(_num(metrics.get("freq_locked")))
    elif isinstance(metrics.get("freq_locked"), bool):
        evidence["freq_locked"] = metrics["freq_locked"]

    # 6) amp_calibrated 显式布尔量
    if isinstance(metrics.get("amp_calibrated"), bool):
        evidence["amp_calibrated"] = metrics["amp_calibrated"]

    return evidence, notes


def describe() -> Dict[str, Any]:
    """自描述：哪些键能换算、口径是什么（``GET /metrics/schema`` 和技能文档共用）。"""
    return {
        "passthrough": list(PASSTHROUGH_NUMERIC),
        "unit_conversions": [
            {"evidence": target, "sources": list(sources), "factor": factor, "note": why}
            for target, sources, factor, why in UNIT_CONVERSIONS
        ],
        "aliases": [{"source": source, "evidence": target} for source, target in ALIASES],
        "contrast_fallbacks": {
            "separation": f"separation ≤ 1 直接用；否则 min(1, separation / {CONTRAST_FROM_SEPARATION:g})",
            "SNR": f"min(1, SNR / {CONTRAST_FROM_SNR:g})",
        },
        "notes": [
            "缺的键不补值：换算不出来的证据就是缺证据。",
            "bool 不当数值用（True 不是幅度）。",
            "f10=0 只在「扫频已执行完成」的意义上记 freq_locked，真机需 f10>0。",
        ],
    }

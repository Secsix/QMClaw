"""双比特 CZ 状态机（pair scope，设计稿 §8）。

载体是「两个比特 + 一条耦合边」，**不是单个比特**：核心证据是两比特间的条件相位
（``cphase``）与漏到 |2⟩ 能级的比例（``cz_leakage``），而不是把单比特指标再报一遍。
所以本模块与 ``qubit_state.py`` 是两套并列的 scope —— 共享同一套判据引擎
（``services.common.metrics``，阈值唯一真源 ``config/lab_criteria.json``）与同一套
证据契约（结论记录带凭据 / 判据版本 / 时效），但状态集与边表独立
（结构在 ``config/state_machine.json`` 的 ``pair_states`` / ``pair_edges``）。

阶段定位（BACKLOG O5 / 设计稿 §13 P4）：

* **结构与判据层：可跑** —— 本模块 + pair 结构表 + 三条 pair 判据；
* **执行层：留 HOLD** —— 厂商 CZ 能力在 ``lqms.measure.tuners.cz_nodes.DCZTuner``
  的类方法上，而 DSL 表达不了「2 比特 + 1 耦合器」这一拓扑
  （``config/lab_criteria.json`` → ``capabilities.two_qubit_vendor.dsl_support = false``）。
  因此 Router 对 pair 的任何 accept / ask_llm 一律改判 ``hold`` + ``dsl_gap``，
  **绝不假装可执行**；等上游仓（``Secsix/OpenQWF``）补上耦合器表达后，把
  ``execution_capability()`` 的判定接上即可放开。

纯函数、无 IO（除了读 json 结构表），可离线单测。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.common.metrics import evaluate, load_criteria
from services.common.state_machine import (
    SCOPE_PAIR,
    evidence_provenance,
    machine_for_scope,
    state_version,
    ttl_for,
)

#: pair scope 的状态序列（设计稿 §8.2，顺序即推进顺序）
PAIR_STATES: List[str] = [
    "pair_ready",
    "cz_point_found",
    "phase_locked",
    "frame_corrected",
    "leakage_bounded",
    "gate_verified",
]

PAIR_STATE_INDEX = {name: i for i, name in enumerate(PAIR_STATES)}

PAIR_STATE_DESCRIPTIONS = {
    "pair_ready": "两比特前置齐备（两个比特都 ≥ amp_calibrated）",
    "cz_point_found": "CZ 工作点已粗定（ZPA 工作点已找到）",
    "phase_locked": "条件相位 = π（cphase 落在 target±tolerance_rad 内）",
    "frame_corrected": "动力学相位已补偿（补偿后残差达标）",
    "leakage_bounded": "泄漏受控（cz_leakage ≤ 上界）",
    "gate_verified": "CZ 门保真度已验证（cz_gate_fidelity ≥ 下界）",
}

PAIR_NEXT_ACTION = {
    "pair_ready": "先跑 CZ 工作点粗定（DCZTuner.initialize_gate / find_zpa_*）；前置比特不足时先把单比特补到 amp_calibrated",
    "cz_point_found": "扫条件相位（sweep_sq_phase）标定到 π",
    "phase_locked": "幅度×时长交替迭代收敛相位（自环，带次数上限）",
    "frame_corrected": "校核泄漏（|2⟩ 能级占比）是否在上界内",
    "leakage_bounded": "跑 CZXEB/IRB 验证门保真度",
    "gate_verified": "CZ 表征完成；掉档时按回退边退回相位标定",
}

#: 每类 pair 证据由哪个厂商动作产出（评审时"缺什么、该跑什么"直接查这张表）
PAIR_EVIDENCE_SOURCES: Dict[str, Dict[str, Any]] = {
    "zpa_point": {
        "produced_by": ["cz.initialize_gate", "cz.find_zpa_c_half_cycle", "cz.find_zpa_q"],
        "note": "ZPA 工作点类证据；对应 DCZTuner.initialize_gate / find_zpa_*",
    },
    "cphase": {
        "produced_by": ["cz.sweep_sq_phase", "cz.swap11to20_sq_phase"],
        "note": "条件相位，判据 config/lab_criteria.json:metrics.cphase（target π + tolerance_rad）",
    },
    "frame_residual_rad": {
        "produced_by": ["cz.sq_phase_xeb", "cz.run_xeb"],
        "note": "动力学相位补偿后的残差；与 cphase 共用同一容差口径",
    },
    "cz_leakage": {
        "produced_by": ["cz.interaction", "cz.swap11to20"],
        "note": "漏到 |2⟩ 能级的占比，判据 metrics.cz_leakage.max_leakage",
    },
    "cz_gate_fidelity": {
        "produced_by": ["cz.run_xeb", "cz.sq_phase_xeb"],
        "note": "CZ 门保真度，判据 metrics.cz_gate_fidelity.min_fidelity；与单比特 xeb.gate_fidelity 区分",
    },
}

#: 每个状态"再往前一步"缺哪些证据（顺序即建议的补齐顺序）
PAIR_MISSING_FOR_STATE: Dict[str, List[str]] = {
    "pair_ready": ["zpa_point"],
    "cz_point_found": ["cphase"],
    "phase_locked": ["frame_residual_rad"],
    "frame_corrected": ["cz_leakage"],
    "leakage_bounded": ["cz_gate_fidelity"],
    "gate_verified": [],
}

#: pair scope 的前置判据：两个比特都必须到这个状态以上（设计稿 §8.2 第 0 行）
PREREQUISITE_STATE = "amp_calibrated"

SUCCESS_STATUSES = ("success", "completed", "qiskit_completed", "ok", "passed", "done")

#: RunRecord.metrics 里的键 → pair 证据键（metric 名可能来自不同实验的命名习惯）
PAIR_METRIC_KEYS: Dict[str, str] = {
    "cphase": "cphase",
    "cphase_rad": "cphase",
    "cz_cphase": "cphase",
    "frame_residual_rad": "frame_residual_rad",
    "cz_frame_residual": "frame_residual_rad",
    "cz_leakage": "cz_leakage",
    "leakage": "cz_leakage",
    "cz_gate_fidelity": "cz_gate_fidelity",
    "cz_fidelity": "cz_gate_fidelity",
}

#: 厂商 CZ 动作名（用于从台账识别"这条记录是 CZ 的"）
PAIR_FUNCTIONS = (
    "initialize_gate",
    "find_zpa_c_half_cycle",
    "find_zpa_q",
    "find_zpa_q_half_cycle",
    "zpa_c_m",
    "zpa_q_m",
    "sweep_sq_phase",
    "swap11to20_sq_phase",
    "gate_time_m",
    "sq_phase_xeb",
    "run_xeb",
    "interaction",
    "swap11to20",
    "tune",
    "zpa_q_czecho",
    "zpa_c_czecho",
    "gate_time_czecho",
)

#: 哪些 CZ 动作能证明"工作点已找到"
ZPA_FUNCTIONS = ("initialize_gate", "find_zpa_c_half_cycle", "find_zpa_q", "find_zpa_q_half_cycle")

_SEPARATORS = re.compile(r"[+,|/\s\-]+")


# ---------------------------------------------------------------------------
# 作用域与前置条件
# ---------------------------------------------------------------------------


def pair_name(qubits: Iterable[str]) -> str:
    """pair 的展示名：``q0+q1``（保留调用方给的顺序：CZ 有 control/target 之分）。"""
    return "+".join(str(q).strip() for q in qubits if str(q).strip())


def pair_key(qubits: Iterable[str]) -> str:
    """pair 的身份键：``q0|q1``（排序后，与顺序无关，用于查表/去重）。"""
    return "|".join(sorted(str(q).strip() for q in qubits if str(q).strip()))


def _qubit_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [part for part in _SEPARATORS.split(value.strip()) if part]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def normalize_pair(payload: Any) -> Dict[str, Any]:
    """把各种入参写法归一成 ``{pair, qubits, coupler}``。

    支持：``{"qubits": ["q0","q1"]}``、``{"pair": "q0+q1"}``、
    ``{"control": "q0", "target": "q1"}``、``{"name": "q0-q1"}``。
    """
    data = payload if isinstance(payload, dict) else {}
    qubits = _qubit_list(data.get("qubits") or data.get("qubit_pair") or data.get("pair"))
    if not qubits and (data.get("control") or data.get("target")):
        qubits = _qubit_list([data.get("control"), data.get("target")])
    if not qubits:
        qubits = _qubit_list(data.get("name"))
    if len(qubits) != 2:
        raise ValueError("pair 需要恰好两个比特（收到 %r）" % (qubits or payload,))
    coupler = data.get("coupler")
    if coupler is not None and not isinstance(coupler, bool):
        try:
            coupler = int(coupler)
        except (TypeError, ValueError):
            raise ValueError("coupler 必须是整数索引（收到 %r）" % (coupler,))
    else:
        codac = None  # noqa: F841 - 只为显式说明 coupler 可缺省
        coupler = None
    return {"pair": pair_name(qubits), "qubits": qubits, "coupler": coupler}


def pair_prerequisites(
    qubit_states: Iterable[Dict[str, Any]],
    threshold: str = PREREQUISITE_STATE,
) -> Dict[str, Any]:
    """两比特前置是否齐备（两个比特都 ≥ ``threshold``）。

    ``qubit_states`` 每项形如 ``{"qubit": "q0", "state": "amp_calibrated"}``；
    只给状态名（字符串）也认。
    """
    from services.common.qubit_state import STATE_INDEX

    need = str(threshold or PREREQUISITE_STATE)
    need_index = int(STATE_INDEX.get(need, 2))
    rows: List[Dict[str, Any]] = []
    for item in qubit_states or []:
        if isinstance(item, dict):
            name = str(item.get("qubit") or item.get("name") or "")
            state = str(item.get("state") or "unknown")
        else:
            name, state = "", str(item or "unknown")
        index = int(STATE_INDEX.get(state, 0))
        rows.append(
            {
                "qubit": name,
                "state": state,
                "state_index": index,
                "ok": index >= need_index,
            }
        )
    short = [row for row in rows if not row["ok"]]
    satisfied = bool(rows) and not short
    if not rows:
        reason = "没有提供前置信道（两个比特的状态未知，按未齐备处理）"
    elif satisfied:
        reason = "两个比特都 ≥ %s" % need
    else:
        reason = "前置未齐备：%s" % "；".join(
            "%s 停在 %s（需 ≥ %s）" % (row["qubit"] or "?", row["state"], need) for row in short
        )
    return {
        "required_state": need,
        "required_state_index": need_index,
        "satisfied": satisfied,
        "per_qubit": rows,
        "shortfall": [
            {"qubit": row["qubit"], "state": row["state"], "required": need} for row in short
        ],
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 证据 → 状态
# ---------------------------------------------------------------------------


def _flag(evidence: Dict[str, Any], key: str) -> bool:
    value = (evidence or {}).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ok", "locked", "done", "found")
    return False


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def derive_pair_state(
    evidence: Optional[Dict[str, Any]] = None,
    criteria: Optional[Dict[str, Any]] = None,
    prerequisites: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """由证据推导 pair 当前状态（设计稿 §8.2 的六态链）。

    ``evidence`` 可用键：``zpa_point``(bool) / ``cphase``(rad) /
    ``frame_residual_rad``(rad) / ``cz_leakage``(ratio) / ``cz_gate_fidelity``(ratio)。

    前置未齐备（``prerequisites.satisfied == False``）时状态**封顶在 pair_ready**：
    单比特都没到 amp_calibrated，CZ 的任何结论都不可信（设计稿的 pair--degrade 边）。
    """
    evidence = dict(evidence or {})
    criteria = criteria or load_criteria()

    reasons: List[str] = []
    metrics: Dict[str, Any] = {}

    ready = True
    if isinstance(prerequisites, dict):
        ready = bool(prerequisites.get("satisfied"))
        if ready:
            reasons.append("前置齐备：%s" % prerequisites.get("reason"))
        else:
            reasons.append("前置未齐备：%s" % prerequisites.get("reason"))

    zpa_ok = _flag(evidence, "zpa_point")
    if zpa_ok:
        reasons.append("zpa_point=True：CZ 工作点已找到")

    def _eval(metric: str, key: str) -> Optional[Dict[str, Any]]:
        value = _number(evidence.get(key))
        if value is None:
            return None
        try:
            verdict = evaluate(metric, value, criteria)
        except KeyError:
            return None
        metrics[metric if key == metric else "%s(%s)" % (metric, key)] = verdict
        return verdict

    cphase_eval = _eval("cphase", "cphase")
    if cphase_eval is not None:
        reasons.append(
            "cphase=%.4f（残差 %.4f rad，%s）"
            % (float(_number(evidence.get("cphase")) or 0.0), cphase_eval.get("residual_rad") or 0.0, cphase_eval["verdict"])
        )
    # 补偿后的残差是"相对 0 的偏移"，不是"离 π 多远"：直接和同一容差比 |残差| ≤ tolerance_rad
    frame_value = _number(evidence.get("frame_residual_rad"))
    frame_eval = None
    if frame_value is not None:
        tolerance = float(
            ((criteria.get("metrics") or {}).get("cphase") or {}).get("tolerance_rad") or 0.05
        )
        frame_ok_value = abs(frame_value) <= tolerance
        frame_eval = {
            "ok": frame_ok_value,
            "verdict": "corrected" if frame_ok_value else "uncorrected",
            "residual_rad": abs(frame_value),
            "tolerance_rad": tolerance,
        }
        metrics["frame_residual_rad"] = frame_eval
        reasons.append(
            "frame_residual_rad=%.4f rad（容差 %.4f，%s）"
            % (abs(frame_value), tolerance, frame_eval["verdict"])
        )
    leak_eval = _eval("cz_leakage", "cz_leakage")
    if leak_eval is not None:
        reasons.append("cz_leakage=%.5f（%s）" % (float(_number(evidence.get("cz_leakage")) or 0.0), leak_eval["verdict"]))
    fid_eval = _eval("cz_gate_fidelity", "cz_gate_fidelity")
    if fid_eval is not None:
        reasons.append("cz_gate_fidelity=%.4f（%s）" % (float(_number(evidence.get("cz_gate_fidelity")) or 0.0), fid_eval["verdict"]))

    phase_ok = bool(zpa_ok and cphase_eval and cphase_eval["ok"])
    frame_ok = bool(phase_ok and frame_eval and frame_eval["ok"])
    leak_ok = bool(frame_ok and leak_eval and leak_eval["ok"])
    verified_ok = bool(leak_ok and fid_eval and fid_eval["ok"])

    if verified_ok:
        state = "gate_verified"
    elif leak_ok:
        state = "leakage_bounded"
    elif frame_ok:
        state = "frame_corrected"
    elif phase_ok:
        state = "phase_locked"
    elif zpa_ok:
        state = "cz_point_found"
    else:
        state = "pair_ready"

    if not ready:
        if state != "pair_ready":
            reasons.append(
                "前置退化 → 结论封顶在 pair_ready（本来满足到 %s，但单比特前置已掉档）" % state
            )
        state = "pair_ready"

    missing: List[str] = list(PAIR_MISSING_FOR_STATE.get(state, []))
    if state == "pair_ready" and not ready:
        missing = ["prerequisites"] + missing

    return {
        "scope": SCOPE_PAIR,
        "state": state,
        "state_index": PAIR_STATE_INDEX[state],
        "description": PAIR_STATE_DESCRIPTIONS[state],
        "reasons": reasons,
        "missing": missing,
        "missing_detail": [
            {"key": key, **dict(PAIR_EVIDENCE_SOURCES.get(key, {}))} for key in missing
        ],
        "next_action": PAIR_NEXT_ACTION[state],
        "metrics": metrics,
        "criteria_version": criteria.get("version"),
        "prerequisites": prerequisites,
        "ready": ready,
    }


# ---------------------------------------------------------------------------
# 结论记录
# ---------------------------------------------------------------------------


def build_pair_record(
    qubits: Iterable[str],
    state_result: Dict[str, Any],
    evidence: Optional[Dict[str, Any]] = None,
    sources: Optional[Dict[str, Any]] = None,
    conflicts: Optional[List[Dict[str, Any]]] = None,
    criteria_version: Optional[str] = None,
    coupler: Optional[int] = None,
    now: Optional[float] = None,
    machine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造 pair 的**状态结论记录**（与 ``build_state_record`` 同契约，scope=pair）。

    结论 = 状态 + 凭据 + 判据版本 + 时效 + 前置。TTL 取 pair 结构表里该状态的 ``ttl_s``。
    """
    import time

    view = machine_for_scope(SCOPE_PAIR, machine)
    state_result = state_result or {}
    state = str(state_result.get("state") or "pair_ready")
    decided_at = float(now if now is not None else time.time())
    ttl = ttl_for(state, view)
    pair = pair_name(qubits)

    evidence_map: Dict[str, Any] = {}
    for key, value in (evidence or {}).items():
        evidence_map[key] = evidence_provenance(key, value, (sources or {}).get(key))

    return {
        # `qubit` 保留成 pair 名，是为了复用 Router 的 `$qubit` 参数替换口径
        "qubit": pair,
        "pair": pair,
        "scope": {
            "type": SCOPE_PAIR,
            "qubits": [str(q) for q in qubits],
            "coupler": coupler,
        },
        "state": state,
        "state_index": state_result.get("state_index", PAIR_STATE_INDEX.get(state)),
        "state_version": state_version(view),
        "criteria_version": criteria_version or state_result.get("criteria_version"),
        "evidence": evidence_map,
        "conflicts": list(conflicts or []),
        "reasons": list(state_result.get("reasons") or []),
        "missing": list(state_result.get("missing") or []),
        "prerequisites": state_result.get("prerequisites"),
        "decided_at": decided_at,
        "expires_at": (decided_at + ttl) if ttl is not None else None,
    }


# ---------------------------------------------------------------------------
# 执行能力（DSL 缺口，不假装可执行）
# ---------------------------------------------------------------------------


def execution_capability(criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """pair（CZ）动作当前能不能落进 DSL 执行。

    真源是 ``config/lab_criteria.json`` → ``capabilities.two_qubit_vendor``：
    ``dsl_support = false`` 表示 DSL 的 qubits 列表描述不了耦合器，执行层必须 HOLD。
    """
    criteria = criteria or load_criteria()
    caps = (criteria.get("capabilities") or {}).get("two_qubit_vendor") or {}
    dsl_support = bool(caps.get("dsl_support"))
    return {
        "dsl_support": dsl_support,
        "executable": dsl_support,
        "requires_dsl_extension": not dsl_support,
        "dsl_gap": caps.get("dsl_gap") or "",
        "vendor": {
            "module": caps.get("module"),
            "class": caps.get("class"),
            "requires_coupler": caps.get("requires_coupler"),
            "coupler_index": caps.get("coupler_index"),
            "methods": list(caps.get("methods") or []),
        },
        "blocked_by": [] if dsl_support else ["dsl:coupler_topology"],
        "note": caps.get("note") or "",
    }


# ---------------------------------------------------------------------------
# 台账 → pair 证据
# ---------------------------------------------------------------------------


def _bare(fn: Any) -> str:
    """去掉模块前缀：``cz.initialize_gate`` / ``DCZTuner.initialize_gate`` → ``initialize_gate``。

    与 ``state_evidence.functions_of`` 的约定一致（那里也不带 ``sq.`` 前缀），
    这样 ``PAIR_FUNCTIONS`` / ``ZPA_FUNCTIONS`` 只写裸方法名就能对上。
    """
    text = str(fn or "").strip()
    if not text:
        return ""
    return text.rsplit(".", 1)[-1]


def _functions_of(record: Dict[str, Any]) -> List[str]:
    manifest = record.get("manifest") or {}
    fns = manifest.get("functions")
    if isinstance(fns, list) and fns:
        return [name for name in (_bare(f) for f in fns) if name]
    program = record.get("program") or ""
    if not program:
        return []
    return [m.group(1) for m in re.finditer(r"\bcz\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", program)]


def _record_time(record: Dict[str, Any]) -> float:
    for key in ("finished_at", "started_at"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def evidence_from_pair_records(
    records: Iterable[Dict[str, Any]],
    qubits: Iterable[str],
) -> Dict[str, Any]:
    """把执行台账翻成 **pair 证据**（只认"两个比特都在记录里且成功"的记录）。

    与单比特的 ``state_evidence.evidence_from_records`` 分工相同：那里认 ``sq.*``
    的函数名与单比特指标，这里只认 CZ 类动作（``cz.*`` / DCZTuner 方法名）与
    ``cphase`` / ``cz_leakage`` / ``cz_gate_fidelity`` 三条指标。
    """
    wanted = {str(q) for q in (qubits or [])}
    evidence: Dict[str, Any] = {}
    sources: Dict[str, Any] = {}
    considered = 0
    ignored = 0
    ignored_runs: List[str] = []

    rows: List[Tuple[float, Dict[str, Any], List[str]]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        rec_qubits = {str(q) for q in (record.get("qubits") or [])}
        if wanted and not wanted <= rec_qubits:
            continue
        rows.append((_record_time(record), record, _functions_of(record)))

    for _, record, fns in sorted(rows, key=lambda item: item[0]):
        status = str(record.get("status") or "").strip().lower()
        run_id = str(record.get("run_id") or "")
        datasets = list(record.get("dataset_ids") or [])
        if status not in SUCCESS_STATUSES or not datasets:
            ignored += 1
            if run_id:
                ignored_runs.append(run_id)
            continue
        considered += 1
        source = {
            "run_id": run_id,
            "dataset_ids": datasets,
            "functions": fns,
            "finished_at": record.get("finished_at"),
            "status": status,
            "source": "cz.%s" % fns[0] if fns else "cz.unknown",
        }
        metrics = record.get("metrics") or {}
        for key, value in metrics.items():
            mapped = PAIR_METRIC_KEYS.get(str(key))
            if not mapped:
                continue
            number = _number(value)
            if number is None:
                continue
            evidence[mapped] = number
            sources[mapped] = dict(source)
        if set(fns) & set(ZPA_FUNCTIONS):
            evidence["zpa_point"] = True
            sources["zpa_point"] = dict(source)

    return {
        "evidence": evidence,
        "sources": sources,
        "considered": considered,
        "ignored": ignored,
        "ignored_runs": ignored_runs[-20:],
    }


# ---------------------------------------------------------------------------
# 表（供文档 / 前端 / 自检引用）
# ---------------------------------------------------------------------------


def state_table_pair() -> Dict[str, Any]:
    """pair 状态机全表（结构与 ``qubit_state.state_table()`` 同形，便于前端复用）。"""
    return {
        "scope": SCOPE_PAIR,
        "states": list(PAIR_STATES),
        "descriptions": dict(PAIR_STATE_DESCRIPTIONS),
        "next_action": dict(PAIR_NEXT_ACTION),
        "evidence_sources": {k: dict(v) for k, v in PAIR_EVIDENCE_SOURCES.items()},
        "missing_by_state": {k: list(v) for k, v in PAIR_MISSING_FOR_STATE.items()},
        "prerequisite_state": PREREQUISITE_STATE,
        "execution": execution_capability(),
    }

"""services/common/templates.py — 模板库与转移概率（纯标准库，可离线单测）。

设计稿 §9 / BACKLOG O4（P3）。三件事：

1. **模板是什么** —— 一份可复用的执行片段 + 适用边 + 可靠性估计 + 晋退状态。
   ``{"id", "edge", "action": {"fn", "params"}, "scope", "prior_success_rate",
   "observed": {"success", "failure"}, "success_rate", "status",
   "promote_threshold", "consecutive_success_for_promote",
   "degrade_threshold", "consecutive_failure_for_degrade", "source"}``。
2. **转移概率** —— 同一个动作在边上的成功率用 Bayesian 平滑：
   ``(success + alpha * prior) / (n + alpha)``，避免小样本假高（§9.2）。
3. **晋退** —— 连续成功达阈值且成功率 ≥ 晋升阈值 → ``promoted``（G1 直接命中，
   免 LLM）；连续失败达阈值或成功率跌破降级阈值 → ``degraded``（降权）；
   长期不用 / 边已删 → ``retired``（出库）。

边界：本模块**不读文件、不连库**（纯函数）。运行期实例的落盘在
``services/common/template_store.py``；结构真源仍是 ``config/state_machine.json``。
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Bayesian 平滑的伪计数（与 router 的 DEFAULT_ALPHA 同义，这里独立给一份）
DEFAULT_ALPHA = 1.0
# 没有经验值时的先验成功率
DEFAULT_PRIOR = 0.6
# 晋升：成功率下界 + 连续成功次数
DEFAULT_PROMOTE_THRESHOLD = 0.8
DEFAULT_SUCCESS_FOR_PROMOTE = 3
# 降级：成功率上界 + 连续失败次数
DEFAULT_DEGRADE_THRESHOLD = 0.3
DEFAULT_FAILURE_FOR_DEGRADE = 2
# 样本太少时不要因为一次失败就把成功率判成"跌破阈值"
DEFAULT_MIN_SAMPLES_FOR_DEGRADE = 2
# 模板状态（§9.3）
STATUSES = ("promoted", "normal", "degraded", "retired")
# RunRecord 的哪些 status 算"成功" / "失败"
SUCCESS_STATUSES = ("success", "completed", "qiskit_completed", "ok", "passed", "done")
FAILURE_STATUSES = ("failed", "failure", "error", "timeout", "aborted", "cancelled", "stuck")
# 每条模板最多留多少条近期结果（审计用，别把文件撑爆）
HISTORY_LIMIT = 20


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_fn(value: Any) -> str:
    """``t1`` / ``sq.t1(q0)`` / `` sq.t1 `` → ``sq.t1``（统一成带前缀的短名）。"""
    text = str(value or "").strip()
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    if not text:
        return ""
    return text if text.startswith("sq.") else "sq." + text


def normalize_template(raw: Any) -> Dict[str, Any]:
    """把一份模板补齐成完整结构（幂等；未登记的字段原样保留）。"""
    template = copy.deepcopy(_as_dict(raw))
    action = _as_dict(template.get("action"))
    fn = normalize_fn(action.get("fn") or action.get("function"))
    action["fn"] = fn
    if not isinstance(action.get("params"), dict):
        action["params"] = _as_dict(action.get("params"))
    template["action"] = action

    edge = str(template.get("edge") or "").strip()
    template["edge"] = edge
    if not template.get("id"):
        template["id"] = auto_template_id(edge, fn, action.get("params"))

    prior = _to_float(template.get("prior_success_rate"), DEFAULT_PRIOR)
    template["prior_success_rate"] = min(1.0, max(0.0, float(prior if prior is not None else DEFAULT_PRIOR)))

    observed = _as_dict(template.get("observed"))
    template["observed"] = {
        "success": max(0, _to_int(observed.get("success"), 0)),
        "failure": max(0, _to_int(observed.get("failure"), 0)),
    }

    template["scope"] = str(template.get("scope") or "qubit")
    status = str(template.get("status") or "normal")
    template["status"] = status if status in STATUSES else "normal"
    template["source"] = str(template.get("source") or "manual")

    template["promote_threshold"] = float(
        _to_float(template.get("promote_threshold"), DEFAULT_PROMOTE_THRESHOLD)
    )
    template["consecutive_success_for_promote"] = max(
        1, _to_int(template.get("consecutive_success_for_promote"), DEFAULT_SUCCESS_FOR_PROMOTE)
    )
    template["degrade_threshold"] = float(
        _to_float(template.get("degrade_threshold"), DEFAULT_DEGRADE_THRESHOLD)
    )
    template["consecutive_failure_for_degrade"] = max(
        1, _to_int(template.get("consecutive_failure_for_degrade"), DEFAULT_FAILURE_FOR_DEGRADE)
    )

    for key in ("consecutive_success", "consecutive_failure"):
        template[key] = max(0, _to_int(template.get(key), 0))
    if not isinstance(template.get("history"), list):
        template["history"] = []

    # success_rate 一律按当前 observed / prior 重算，不接受外部直接写死
    template["success_rate"] = success_rate(template)
    return template


def auto_template_id(edge: str, fn: str, params: Any = None) -> str:
    """没给 id 时按「边 + 动作 + 参数」生成稳定 id（同输入永远同 id）。"""
    payload = json.dumps(
        {"edge": edge or "", "fn": fn or "", "params": params or {}},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:8]
    return "T-auto-%s" % digest


def success_rate(template: Any, alpha: Optional[float] = None) -> float:
    """Bayesian 平滑后的成功率：``(success + α·prior) / (success + failure + α)``。"""
    template = _as_dict(template)
    observed = _as_dict(template.get("observed"))
    success = max(0, _to_int(observed.get("success"), 0))
    failure = max(0, _to_int(observed.get("failure"), 0))
    prior = _to_float(template.get("prior_success_rate"), DEFAULT_PRIOR)
    prior = DEFAULT_PRIOR if prior is None else min(1.0, max(0.0, prior))
    if alpha is None:
        alpha = _to_float(template.get("alpha"), DEFAULT_ALPHA)
    alpha = DEFAULT_ALPHA if alpha is None or alpha <= 0 else float(alpha)
    total = success + failure
    return round((success + alpha * prior) / (total + alpha), 6)


def is_stable(template: Any) -> bool:
    """G1 的"近期稳定"：成功率够高，且连续成功次数够（或已被晋升）。"""
    template = _as_dict(template)
    rate = _to_float(template.get("success_rate"))
    if rate is None:
        rate = success_rate(template)
    threshold = _to_float(template.get("promote_threshold"), DEFAULT_PROMOTE_THRESHOLD)
    streak_needed = max(1, _to_int(template.get("consecutive_success_for_promote"), DEFAULT_SUCCESS_FOR_PROMOTE))
    streak = max(0, _to_int(template.get("consecutive_success"), 0))
    if threshold is None:
        threshold = DEFAULT_PROMOTE_THRESHOLD
    if rate < float(threshold):
        return False
    return streak >= streak_needed or str(template.get("status")) == "promoted"


def evaluate_status(template: Any) -> str:
    """按 §9.3 判定模板该处于哪个状态（不改入参）。"""
    template = normalize_template(template) if "success_rate" not in _as_dict(template) else _as_dict(template)
    current = str(template.get("status") or "normal")
    if current == "retired":
        return "retired"
    rate = _to_float(template.get("success_rate"))
    if rate is None:
        rate = success_rate(template)
    observed = _as_dict(template.get("observed"))
    total = _to_int(observed.get("success"), 0) + _to_int(observed.get("failure"), 0)
    promote_threshold = _to_float(template.get("promote_threshold"), DEFAULT_PROMOTE_THRESHOLD)
    degrade_threshold = _to_float(template.get("degrade_threshold"), DEFAULT_DEGRADE_THRESHOLD)
    promote_threshold = DEFAULT_PROMOTE_THRESHOLD if promote_threshold is None else promote_threshold
    degrade_threshold = DEFAULT_DEGRADE_THRESHOLD if degrade_threshold is None else degrade_threshold
    success_streak = max(0, _to_int(template.get("consecutive_success"), 0))
    failure_streak = max(0, _to_int(template.get("consecutive_failure"), 0))
    promote_streak = max(1, _to_int(template.get("consecutive_success_for_promote"), DEFAULT_SUCCESS_FOR_PROMOTE))
    degrade_streak = max(1, _to_int(template.get("consecutive_failure_for_degrade"), DEFAULT_FAILURE_FOR_DEGRADE))

    if success_streak >= promote_streak and rate >= promote_threshold:
        return "promoted"
    if failure_streak >= degrade_streak:
        return "degraded"
    if total >= DEFAULT_MIN_SAMPLES_FOR_DEGRADE and rate < degrade_threshold:
        return "degraded"
    return "normal"


def apply_outcome(
    template: Any,
    success: bool,
    now: Optional[float] = None,
    run_id: Optional[str] = None,
    alpha: Optional[float] = None,
) -> Dict[str, Any]:
    """记一次结果并重算晋退（返回**新字典**，不改入参）。

    返回的模板带 ``last_transition``（如 ``"normal->promoted"``，无变化为空串）
    和 ``last_outcome``，方便调用方审计"这次谁被晋升/降级了"。
    """
    current = normalize_template(template)
    if current["status"] == "retired":
        current["last_transition"] = ""
        current["last_outcome"] = None
        return current

    succeeded = bool(success)
    observed = dict(current["observed"])
    if succeeded:
        observed["success"] += 1
        current["consecutive_success"] = _to_int(current.get("consecutive_success"), 0) + 1
        current["consecutive_failure"] = 0
    else:
        observed["failure"] += 1
        current["consecutive_failure"] = _to_int(current.get("consecutive_failure"), 0) + 1
        current["consecutive_success"] = 0
    current["observed"] = observed
    current["success_rate"] = success_rate(current, alpha=alpha)

    stamp = float(now if now is not None else time.time())
    current["last_used_at"] = stamp
    if run_id:
        current["last_run_id"] = str(run_id)
    history = list(current.get("history") or [])
    history.append({"run_id": run_id, "success": succeeded, "at": stamp})
    current["history"] = history[-HISTORY_LIMIT:]

    before = str(current.get("status") or "normal")
    after = evaluate_status(current)
    current["status"] = after
    current["last_transition"] = "" if after == before else "%s->%s" % (before, after)
    current["last_outcome"] = {"success": succeeded, "run_id": run_id, "at": stamp}
    return current


def retire(template: Any, reason: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    """出库（人工或"边已从结构里删掉"）：status → retired，保留历史。"""
    current = normalize_template(template)
    if current["status"] != "retired":
        current["last_transition"] = "%s->retired" % current["status"]
        current["status"] = "retired"
        current["retired_reason"] = str(reason or "")
        current["retired_at"] = float(now if now is not None else time.time())
    return current


def retire_stale(
    templates: Iterable[Any],
    max_idle_seconds: Optional[float] = None,
    now: Optional[float] = None,
    machine: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """把「长期没用」或「边已不在结构里」的模板标成 retired（§9.3 第三种事件）。"""
    stamp = float(now if now is not None else time.time())
    edges = None
    if machine is not None:
        edges = {
            str(_as_dict(edge).get("id") or "")
            for edge in (machine.get("edges") or [])
        }
    out: List[Dict[str, Any]] = []
    for raw in templates:
        template = normalize_template(raw)
        if template["status"] == "retired":
            out.append(template)
            continue
        reason = ""
        if edges is not None and template.get("edge") and template["edge"] not in edges:
            reason = "edge %s no longer in state_machine" % template["edge"]
        elif max_idle_seconds is not None:
            last = _to_float(template.get("last_used_at"))
            if last is None:
                created = _to_float(template.get("created_at"))
                last = created
            if last is not None and stamp - float(last) > float(max_idle_seconds):
                reason = "idle for %.0fs" % (stamp - float(last))
        out.append(retire(template, reason, now=stamp) if reason else template)
    return out


def rank(templates: Iterable[Any], edge: Optional[str] = None) -> List[Dict[str, Any]]:
    """按平滑成功率排序（只做排序，不剪枝——剪枝是 Router 的职责）。"""
    items = [normalize_template(t) for t in templates]
    if edge:
        items = [t for t in items if t.get("edge") == edge]
    items = [t for t in items if t.get("status") != "retired"]
    items.sort(key=lambda t: (-float(t.get("success_rate") or 0.0), str(t.get("id") or "")))
    return items


def templates_from_machine(machine: Any) -> List[Dict[str, Any]]:
    """读 ``machine["templates"]`` 并归一化（跳过 retired）。"""
    out: List[Dict[str, Any]] = []
    for raw in (_as_dict(machine).get("templates") or []):
        template = normalize_template(raw)
        if template["status"] == "retired":
            continue
        out.append(template)
    return out


def merge_templates(base: Iterable[Any], overlay: Iterable[Any]) -> List[Dict[str, Any]]:
    """按 id 合并：overlay（运行期实例）优先，base 里多出来的保留。"""
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for raw in base:
        template = normalize_template(raw)
        if template["id"] not in merged:
            order.append(template["id"])
        merged[template["id"]] = template
    for raw in overlay:
        template = normalize_template(raw)
        if template["id"] not in merged:
            order.append(template["id"])
        merged[template["id"]] = template
    return [merged[key] for key in order]


def outcome_from_record(record: Any) -> Optional[bool]:
    """从 RunRecord 判一次结果：成功要有数据集，失败看 status，其余返回 None（不计）。"""
    record = _as_dict(record)
    status = str(record.get("status") or "").strip().lower()
    if status in FAILURE_STATUSES:
        return False
    if status not in SUCCESS_STATUSES:
        return None
    datasets = record.get("dataset_ids")
    if isinstance(datasets, (list, tuple)) and len(datasets) > 0:
        return True
    return None


def infer_outcomes(
    templates: Iterable[Any],
    records: Iterable[Any],
) -> Dict[str, Any]:
    """把台账翻成「模板 → 结果」序列（歧义的不猜，记进 skipped）。"""
    items = [normalize_template(t) for t in templates if normalize_template(t)["status"] != "retired"]
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    ordered = sorted(
        (_as_dict(r) for r in records),
        key=lambda r: float(_to_float(r.get("started_at"), 0.0) or 0.0),
    )
    for record in ordered:
        run_id = record.get("run_id")
        success = outcome_from_record(record)
        fns = {normalize_fn(fn) for fn in (record.get("functions") or []) if fn}
        if not fns:
            # 没写 functions 就退回 manifest / program 里的 sq.<fn>(（复用现有解析）
            try:
                from services.common.state_evidence import functions_of

                fns = {normalize_fn(fn) for fn in functions_of(record) if fn}
            except Exception:
                fns = set()
        if not fns:
            skipped.append({"run_id": run_id, "reason": "no_functions"})
            continue
        if success is None:
            # 区分两种不计入：状态本身不是成功/失败（多半是离线占位），
            # 还是"成功了但没写数据集"（§9.2 要求有数据才算数）—— 后者在台账里
            # 很常见（qiskit_completed 但没有 dataset_ids），单独标出来才好排查。
            status = str(record.get("status") or "").strip().lower()
            reason = (
                "success_without_dataset"
                if status in SUCCESS_STATUSES
                else "status_unknown_or_no_dataset"
            )
            skipped.append({"run_id": run_id, "reason": reason, "status": record.get("status")})
            continue
        matches = [t for t in items if normalize_fn((t.get("action") or {}).get("fn")) in fns]
        if not matches:
            skipped.append({"run_id": run_id, "reason": "no_template"})
            continue
        if len(matches) > 1:
            skipped.append({
                "run_id": run_id,
                "reason": "ambiguous_functions",
                "functions": sorted(fns),
                "matched": [t["id"] for t in matches],
            })
            continue
        template = matches[0]
        applied.append({
            "template_id": template["id"],
            "edge": template.get("edge"),
            "success": bool(success),
            "run_id": run_id,
            "at": _to_float(record.get("finished_at")) or _to_float(record.get("started_at")),
        })
    return {"applied": applied, "skipped": skipped}


def update_from_records(
    templates: Iterable[Any],
    records: Iterable[Any],
    alpha: Optional[float] = None,
) -> Dict[str, Any]:
    """台账 → 概率更新（§9.2）：把唯一对应的结果累加进模板并重算晋退。"""
    items = [normalize_template(t) for t in templates]
    inferred = infer_outcomes(items, records)
    by_id = {t["id"]: t for t in items}
    transitions: List[Dict[str, Any]] = []
    for outcome in inferred["applied"]:
        template = by_id.get(outcome["template_id"])
        if template is None:
            continue
        updated = apply_outcome(
            template,
            outcome["success"],
            now=outcome.get("at"),
            run_id=outcome.get("run_id"),
            alpha=alpha,
        )
        by_id[updated["id"]] = updated
        if updated.get("last_transition"):
            transitions.append({
                "template_id": updated["id"],
                "transition": updated["last_transition"],
                "success_rate": updated["success_rate"],
                "run_id": outcome.get("run_id"),
            })
    return {
        "templates": [by_id[t["id"]] for t in items],
        "applied": inferred["applied"],
        "skipped": inferred["skipped"],
        "transitions": transitions,
    }


def from_rules(rules_doc: Any, machine: Any) -> Dict[str, Any]:
    """老 ``config/rules.json`` → 模板候选（§9.4 兼容读取，默认不落盘）。

    rules.json 是"症状 → 推荐函数"的表，不是状态机：这里只把它当作模板**候选**，
    只有当某个 fn 在结构表里**恰好落在一条边**上时才给出模板；否则记进 skipped，
    避免把同一个函数（如 sq.spectroscopy 同时属于 3 条边）硬塞进错误的状态转移。
    """
    edges = [_as_dict(e) for e in (_as_dict(machine).get("edges") or [])]
    rules = (_as_dict(rules_doc).get("rules") or [])
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    # 同一个 (edge, fn, params) 只留一份：rules.json 里多条规则常常推荐同一个动作
    # （如 t1_short / t2_short 都推荐 sq.ramsey_df），重复模板只会让 G1 匹配结果摇摆。
    seen: Dict[str, str] = {}

    for rule in rules:
        rule = _as_dict(rule)
        name = str(rule.get("name") or "rule")
        recs = [_as_dict(r) for r in (rule.get("recommendations") or [])]
        recs.sort(key=lambda r: _to_int(r.get("priority"), 99))
        for rec in recs:
            fn = normalize_fn(rec.get("fn"))
            if not fn:
                continue
            owners = [
                edge for edge in edges
                if normalize_fn((_as_dict(edge.get("action"))).get("fn")) == fn
                or fn in {
                    normalize_fn(alt)
                    for alt in ((_as_dict(edge.get("action"))).get("alternatives") or [])
                }
            ]
            if len(owners) != 1:
                skipped.append({
                    "rule": name,
                    "fn": fn,
                    "reason": "edge_ambiguous" if owners else "edge_not_found",
                    "edges": [str(_as_dict(e).get("id")) for e in owners],
                })
                continue
            edge_id = str(_as_dict(owners[0]).get("id"))
            dedupe_key = "%s|%s|%s" % (edge_id, fn, json.dumps(rec.get("params") or {}, sort_keys=True))
            if dedupe_key in seen:
                skipped.append({
                    "rule": name,
                    "fn": fn,
                    "reason": "duplicate_edge_fn",
                    "edges": [edge_id],
                    "kept": seen[dedupe_key],
                })
                continue
            seen[dedupe_key] = name
            candidate = normalize_template({
                "id": "T-rule-%s" % hashlib.sha1(
                    ("%s|%s" % (name, fn)).encode("utf-8", "replace")
                ).hexdigest()[:8],
                "edge": edge_id,
                "action": {"fn": fn, "params": {}},
                "scope": "qubit",
                "prior_success_rate": 0.5,
                "observed": {"success": 0, "failure": 0},
                "status": "normal",
                "source": "manual",
                "note": "migrated from config/rules.json: %s（%s）" % (
                    name, str(rule.get("condition") or ""),
                ),
                "symptom": str(rule.get("symptom") or ""),
                "reason": str(rec.get("reason") or ""),
            })
            created.append(candidate)
    return {"templates": created, "skipped": skipped}


def stats(templates: Iterable[Any]) -> Dict[str, Any]:
    """模板库概况（给接口/前端看）。"""
    items = [normalize_template(t) for t in templates]
    by_status: Dict[str, int] = {status: 0 for status in STATUSES}
    by_edge: Dict[str, int] = {}
    for template in items:
        by_status[template["status"]] = by_status.get(template["status"], 0) + 1
        edge = str(template.get("edge") or "")
        by_edge[edge] = by_edge.get(edge, 0) + 1
    return {
        "count": len(items),
        "by_status": by_status,
        "by_edge": by_edge,
        "promoted": by_status.get("promoted", 0),
    }

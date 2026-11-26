#!/usr/bin/env python3
"""tools/measurement_loop.py — 状态驱动的测控循环（驾驭层执行端）。

和 `demo_control_console.py` 的区别，一句话：

    六阶段剧本 = 照着清单跑；这个循环 = 每轮现问状态机「现在能做什么」。

每轮只有四步，顺序不许换：

    ① POST /api/quantum/qubit/state   → 当前态（结构版本 / 判据版本 / 证据）
    ② POST /api/quantum/qubit/route   → 下一步（不传 actions，服务端按 A(s) 生成候选）
    ③ 按 decision 分支：accept 直接跑 / ask_llm 停下交人 / reject 什么都不跑 / hold 停下
    ④ 只有 execute 分支才：提交 QWF → 等运行 → 取指标 → 换算 → 落证据（source=loop）

退出码约定（DSH 驾驭层靠它判断该不该停下来问人）：

    0  execute（继续下一轮，或已到终态）
    2  rejected  —— G0：动作不在 A(s) 内 / 当前态没有合法边，一个实验都没发
    3  needs_decision —— G3：模板库没有可用写法，候选已打印，等人指定
    4  hold      —— G4：闸门拦截 / 写授权关闭 / 自环次数用尽
    5  服务不可用（:3002 / :3003 / :3008 没起来，或接口报错）

两条不做的事：

- **不猜参数**：`--decide-params` 不给就只传动作本身的 params（通常是空的）。
  `sq.spectroscopy` 的 freq、`sq.t1` 的 zpa 这类真机必需参数，脚本只提示；
  要强制拦下来就用 `--strict-params`（模拟后端不传也能跑，所以默认不拦）。
- **不假装台账证据**：模拟后端的 run 没有 dataset_ids，台账通道看不见它们，
  所以只能走演练证据通道（标 source=loop）。看板上会显示黄色「演练证据」标记，
  谁看都知道这不是真机实测。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional

try:  # 作为脚本运行：python server/tools/measurement_loop.py
    from demo_control_console import (  # type: ignore
        DEFAULT_BASE,
        DEFAULT_TIMEOUT,
        DEFAULT_TTL_S,
        _fmt_seconds,
        collect_metrics,
        metrics_to_evidence,
        post_json,
        read_state,
        state_name,
        submit_qwf,
        wait_run,
        write_evidence,
    )
except ImportError:  # pragma: no cover - 作为包的一部分被导入时
    from .demo_control_console import (  # type: ignore
        DEFAULT_BASE,
        DEFAULT_TIMEOUT,
        DEFAULT_TTL_S,
        _fmt_seconds,
        collect_metrics,
        metrics_to_evidence,
        post_json,
        read_state,
        state_name,
        submit_qwf,
        wait_run,
        write_evidence,
    )


# ── 常量 ──────────────────────────────────────────────────────────────────

TERMINAL_STATE = "characterized"

KIND_EXECUTE = "execute"
KIND_NEEDS_DECISION = "needs_decision"
KIND_REJECTED = "rejected"
KIND_HOLD = "hold"
KIND_DONE = "done"

EXIT_CODES: Dict[str, int] = {
    KIND_DONE: 0,
    KIND_EXECUTE: 0,
    KIND_REJECTED: 2,
    KIND_NEEDS_DECISION: 3,
    KIND_HOLD: 4,
}
EXIT_SERVICE_DOWN = 5

#: 同一状态连续这么多轮没推进就停下（模拟后端「跑了但证据没被采纳」的兜底）
MAX_STALLED_ROUNDS = 2

#: 每个实验的语句超时（秒）。T1 / XEB 的预检估算动辄几十分钟，给小了会被判超时跳过。
FUNCTION_TIMEOUTS: Dict[str, int] = {
    "sq.iqraw": 120,
    "sq.spectroscopy": 900,
    "sq.spectroscopy_iq": 900,
    "sq.pidf": 900,
    "sq.piamp": 300,
    "sq.time_rabi": 300,
    "sq.rabi": 300,
    "sq.ramsey_df": 300,
    "sq.ramsey": 300,
    "sq.t1": 4200,
    "sq.xeb": 1500,
}
DEFAULT_FUNCTION_TIMEOUT = 300

#: 需要的 wall clock 预算（跟 timeout 成对，写进 QWF 的 budget）
FUNCTION_BUDGETS: Dict[str, str] = {
    "sq.iqraw": "5m",
    "sq.spectroscopy": "15m",
    "sq.piamp": "8m",
    "sq.time_rabi": "8m",
    "sq.ramsey_df": "10m",
    "sq.ramsey": "10m",
    "sq.t1": "90m",
    "sq.xeb": "40m",
}
DEFAULT_BUDGET = "10m"

#: 真机必需的数值参数（键 → 为什么要）。
PARAM_HINTS: Dict[str, Dict[str, str]] = {
    "sq.spectroscopy": {"freq": "扫频要显式给频段（例 {\"freq\": [4.0, 3.0, -0.002]}），否则退化成标量"},
    "sq.spectroscopy_iq": {"freq": "扫频要显式给频段"},
    "sq.t1": {"zpa": "T1 要固定 zpa 才是 1D 时间扫描"},
    "sq.time_rabi": {"amp": "幅度扫描要给起始幅度"},
}

EVIDENCE_SOURCE = "loop"


# ── 纯函数（单测对象，不碰网络） ──────────────────────────────────────────


def bare_fn(fn: Any) -> str:
    """`sq.t1(delay=1)` / ` sq.t1 ` → `sq.t1`。"""
    text = str(fn or "").strip()
    text = text.split("(")[0].strip()
    return text


def allowed_actions(route: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """route 结果里「本轮允许的动作」。G3 看 llm_task，其余看 chosen_action。"""
    if not isinstance(route, dict):
        return []
    actions: List[Dict[str, Any]] = []
    task = route.get("llm_task")
    if isinstance(task, dict):
        for item in task.get("allowed_actions") or []:
            if isinstance(item, dict):
                actions.append(dict(item))
            elif item:
                actions.append({"fn": bare_fn(item)})
    chosen = route.get("chosen_action")
    if isinstance(chosen, dict) and chosen.get("fn"):
        fn = bare_fn(chosen["fn"])
        if not any(bare_fn(a.get("fn")) == fn for a in actions):
            actions.append(
                {
                    "fn": fn,
                    "edge_id": chosen.get("edge_id"),
                    "params": chosen.get("params") or {},
                    "intent": "route 已定案（模板命中）",
                }
            )
    return actions


def find_action(
    route: Optional[Dict[str, Any]],
    fn: Optional[str] = None,
    edge_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """人指定的动作是否真在候选里。不在就返回 None —— 不给越界开后门。"""
    if not fn and not edge_id:
        return None
    for action in allowed_actions(route):
        if fn and bare_fn(action.get("fn")) != bare_fn(fn):
            continue
        if edge_id and str(action.get("edge_id") or "") != str(edge_id):
            continue
        return action
    return None


def _merge_params(action_params: Any, extra: Optional[Dict[str, Any]], qubit: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if isinstance(action_params, dict):
        params.update(action_params)
    if isinstance(extra, dict):
        params.update(extra)
    if qubit:
        for key, value in list(params.items()):
            if value == "$qubit":
                params[key] = qubit
    return params


def plan_round(
    route: Dict[str, Any],
    current_state: Optional[str] = None,
    *,
    qubit: str = "",
    decide_fn: Optional[str] = None,
    decide_edge: Optional[str] = None,
    decide_params: Optional[Dict[str, Any]] = None,
    strict_params: bool = False,
    terminal: str = TERMINAL_STATE,
) -> Dict[str, Any]:
    """把一次 route 结果翻译成「这一轮干什么」。纯函数，不执行任何实验。"""
    state = str(
        current_state
        or route.get("current_state")
        or (route.get("state") if isinstance(route.get("state"), str) else "")
        or "unknown"
    )
    plan: Dict[str, Any] = {
        "kind": KIND_NEEDS_DECISION,
        "state": state,
        "decision": str(route.get("decision") or ""),
        "branch": route.get("branch"),
        "fn": None,
        "edge_id": None,
        "params": {},
        "action_kind": route.get("action_kind"),
        "allowed": allowed_actions(route),
        "missing": route.get("missing") or {},
        "note": "",
    }

    if state == terminal:
        plan["kind"] = KIND_DONE
        plan["note"] = f"已经是 {terminal}，没有更深的态可推进"
        return plan

    if not route.get("success", True):
        plan["kind"] = KIND_HOLD
        plan["note"] = f"route 没返回结果：{route.get('error') or route.get('error_code') or '未知错误'}"
        return plan

    decision = plan["decision"].lower()

    if decision == "reject":
        plan["kind"] = KIND_REJECTED
        plan["note"] = str(route.get("reason") or "动作不在当前态的合法边内")
        return plan

    if decision == "hold":
        blocked = route.get("blocked_by") or []
        bits = [str(route.get("reason") or "route 判定 hold")]
        if blocked:
            bits.append("闸门：" + "、".join(str(b) for b in blocked))
        if route.get("requires_authorization") and "授权" not in bits[0]:
            bits.append("写授权未开（allow_execute / allow_set_params 均为 false）")
        if route.get("needs_human"):
            bits.append("需要人工确认现场安全")
        plan["kind"] = KIND_HOLD
        plan["note"] = "；".join(bits)
        return plan

    if decision == "accept":
        chosen = route.get("chosen_action") if isinstance(route.get("chosen_action"), dict) else {}
        fn = bare_fn(chosen.get("fn"))
        if not fn:
            plan["kind"] = KIND_HOLD
            plan["note"] = "route 判定 accept 但没给出动作，按异常处理"
            return plan
        plan["fn"] = fn
        plan["edge_id"] = chosen.get("edge_id")
        plan["params"] = _merge_params(chosen.get("params"), decide_params, qubit)
        plan["action_kind"] = route.get("action_kind") or chosen.get("action_kind")
        plan["kind"] = KIND_EXECUTE
        plan["note"] = f"模板命中（{plan['branch']}），不需要人挑"
    elif decision == "ask_llm":
        action: Optional[Dict[str, Any]] = None
        if decide_fn or decide_edge:
            action = find_action(route, decide_fn, decide_edge)
            if action is None:
                want = decide_fn or decide_edge
                plan["kind"] = KIND_NEEDS_DECISION
                plan["note"] = f"指定的 {want} 不在 allowed_actions 内，拒绝执行（只用合法边内的动作）"
                return plan
        else:
            plan["kind"] = KIND_NEEDS_DECISION
            plan["note"] = "模板库没有适用写法（G3），候选已列出，等你指定 --decide-fn"
            return plan

        plan["fn"] = bare_fn(action.get("fn"))
        plan["edge_id"] = action.get("edge_id")
        plan["params"] = _merge_params(action.get("params"), decide_params, qubit)
        plan["action_kind"] = route.get("action_kind")
        plan["kind"] = KIND_EXECUTE
        plan["note"] = f"按你指定选 {plan['fn']}（{plan['edge_id']}）"
    else:
        plan["kind"] = KIND_NEEDS_DECISION
        plan["note"] = f"未知 decision={plan['decision'] or '（空）'}，不敢自己走"
        return plan

    if strict_params:
        hints = PARAM_HINTS.get(str(plan["fn"])) or {}
        missing = [key for key in hints if key not in plan["params"]]
        if missing:
            detail = "；".join(f"{key}：{hints[key]}" for key in missing)
            plan["kind"] = KIND_NEEDS_DECISION
            plan["note"] = f"{plan['fn']} 缺必需参数（{detail}）；用 --decide-params 给值"
    return plan


def exit_code_for(kind: str) -> int:
    """plan 的 kind → 进程退出码。"""
    return EXIT_CODES.get(str(kind), EXIT_NEEDS_CODE)


EXIT_NEEDS_CODE = EXIT_CODES[KIND_NEEDS_DECISION]


def _arg_text(value: Any) -> str:
    """把 Python 值写成 QWF args 能解析的字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return "range " + " ".join(_arg_text(item) for item in value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./+-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def render_args(params: Optional[Dict[str, Any]]) -> str:
    if not isinstance(params, dict) or not params:
        return ""
    parts = [f"{key} {_arg_text(value)}" for key, value in params.items()]
    return "    args { " + "  ".join(parts) + " }\n"


def qwf_slug(fn: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", bare_fn(fn)).strip("_") or "measure"


def build_qwf(
    fn: str,
    qubit: str,
    params: Optional[Dict[str, Any]] = None,
    note: str = "",
    *,
    workflow_id: Optional[str] = None,
    timeout: Optional[int] = None,
    budget: Optional[str] = None,
) -> str:
    """生成一份单节点 QWF 文本（结构检查能过：meta/env/budget/measure/say 齐全）。"""
    fn = bare_fn(fn)
    workflow_id = workflow_id or f"loop_{qwf_slug(fn)}_{int(time.time() * 1000)}"
    timeout = timeout or FUNCTION_TIMEOUTS.get(fn, DEFAULT_FUNCTION_TIMEOUT)
    budget = budget or FUNCTION_BUDGETS.get(fn, DEFAULT_BUDGET)
    label = (note or f"循环选的实验：{fn}").replace('"', "'")
    return (
        f"# 由 measurement_loop.py 生成：按状态机 route() 的判定选出的下一步\n"
        f"workflow {workflow_id} {{\n"
        f"  meta {{\n"
        f'    name        "{workflow_id}"\n'
        f'    description "{fn} @ {qubit}"\n'
        f'    tags        ["loop", "{fn}"]\n'
        f"  }}\n"
        f"  env {{\n"
        f'    qubit "{qubit}"\n'
        f"  }}\n"
        f"  budget {{\n"
        f"    max_wall     {budget}\n"
        f"    max_writes   2\n"
        f"    max_datasets 2\n"
        f"  }}\n"
        f"  measure as m1 {{\n"
        f"    fn      {fn}\n"
        f"    qubit   $env.qubit\n"
        f"{render_args(params)}"
        f"    timeout {timeout}\n"
        f'    note    "{label}"\n'
        f"  }}\n"
        f'  say "{fn} 完成：数据集 ${{m1.dataset}}"\n'
        f"}}\n"
    )


# ── 主循环 ────────────────────────────────────────────────────────────────


def _print_plan(plan: Dict[str, Any], round_index: int, rounds: int, qubit: str) -> None:
    print(f"\n[轮 {round_index}/{rounds}] {qubit} 当前 {plan['state']}")
    if plan["kind"] == KIND_DONE:
        print(f"[路由] {plan['note']}")
        print("[完成] 不必再跑实验")
        return
    branch = plan.get("branch") or "—"
    print(f"[路由] decision={plan['decision'] or '—'} / branch={branch} · {plan['note']}")
    if plan["kind"] in (KIND_NEEDS_DECISION, KIND_REJECTED, KIND_HOLD):
        allowed = plan.get("allowed") or []
        if allowed:
            print("[候选] 只能在下面这些里选（都来自当前态的合法边）：")
            for item in allowed:
                print(
                    f"       · {item.get('fn')}  ←  {item.get('edge_id') or '—'}"
                    + (f"   （{item.get('intent')}）" if item.get("intent") else "")
                )
            print("[下一步] 我来选：python server/tools/measurement_loop.py "
                  f"--qubit {qubit} --decide-fn <上面的 fn> [--decide-params '{{...}}']")
        missing = plan.get("missing") or {}
        if isinstance(missing, dict):
            states = missing.get("states") or []
            edges = missing.get("edges") or []
            if states:
                print(f"[缺口] 还需要这些状态：{', '.join(str(s) for s in states)}")
            if edges:
                print(f"[缺口] 还需要这些边：{', '.join(str(e) for e in edges)}")
    elif plan["kind"] == KIND_EXECUTE:
        print(f"[选择] {plan['fn']}（{plan.get('edge_id') or '—'}）· 写语义 {plan.get('action_kind') or '—'}")


def _execute_round(
    base: str,
    qubit: str,
    plan: Dict[str, Any],
    *,
    ttl_s: float,
    timeout: float,
    run_timeout: float,
    dry_run: bool,
) -> Dict[str, Any]:
    text = build_qwf(plan["fn"], qubit, plan.get("params"), note=f"循环：{plan.get('edge_id') or plan['fn']}")
    if dry_run:
        print("[dry-run] 将提交下面这份 QWF（不真的跑）：")
        print(text)
        return {"workflow_id": "", "run_status": None, "metrics": {}, "plots": [], "evidence": {}, "notes": [], "state_after": plan["state"]}

    workflow_id, _ir = submit_qwf(base, text, qubit, timeout)
    print(f"[提交] {workflow_id}（/control ② 会显示这份流程图）")
    run = wait_run(base, workflow_id, run_timeout)
    status = str(run.get("status") or "").lower()
    metrics, plots = collect_metrics(run)
    runtime = run.get("totalDuration") or 0
    spent = f"{runtime / 1000:.0f}s" if runtime else "—"
    print(f"[运行] {run.get('status')} · 耗时 {spent} · 图像 {len(plots)} 张"
          + (f" · 指标 {', '.join(sorted(metrics))}" if metrics else " · 没给指标"))
    outcome = run.get("outcome") if isinstance(run.get("outcome"), dict) else {}
    if outcome.get("reason") and status not in ("completed", "ok", "success"):
        print(f"[原因] {outcome.get('reason')}")
    for line in plots:
        print(f"[图像] {line}")

    evidence: Dict[str, Any] = {}
    notes: List[str] = []
    if status in ("completed", "ok", "success"):
        evidence, notes = metrics_to_evidence(metrics)
        for line in notes:
            print(f"[换算] {line}")
    else:
        print(f"[证据] 本次运行状态是 {run.get('status')}，不写证据（不猜指标）")

    if evidence:
        written = write_evidence(
            base,
            qubit,
            evidence,
            ttl_s=ttl_s,
            note=f"循环第 N 轮：{plan['fn']} via {plan.get('edge_id')}",
            run_id=workflow_id,
            timeout=timeout,
            source=EVIDENCE_SOURCE,
        )
        if written.get("success"):
            print(f"[证据] 已写入 {written.get('count')} 条 · source={EVIDENCE_SOURCE} · ttl {_fmt_seconds(ttl_s)}")
        else:
            print(f"[证据] 写入失败：{written.get('error')}")
    return {
        "workflow_id": workflow_id,
        "run_status": run.get("status"),
        "metrics": metrics,
        "plots": plots,
        "evidence": evidence,
        "notes": notes,
    }


def run_loop(
    base: str = DEFAULT_BASE,
    qubit: str = "q0",
    *,
    max_rounds: int = 8,
    interval: float = 0.0,
    ttl_s: float = DEFAULT_TTL_S,
    timeout: float = DEFAULT_TIMEOUT,
    run_timeout: float = 600.0,
    decide_fn: Optional[str] = None,
    decide_edge: Optional[str] = None,
    decide_params: Optional[Dict[str, Any]] = None,
    strict_params: bool = False,
    dry_run: bool = False,
    reset: bool = False,
    verbose: bool = True,
) -> int:
    """跑循环，返回进程退出码（0/2/3/4/5）。"""
    base = base.rstrip("/")
    if reset:
        cleared = post_json(base, "/api/quantum/qubit/evidence/reset", {"name": qubit}, timeout=30)
        print(f"[重置] 已清空 {qubit} 的演练证据：{cleared.get('removed') or []}")
    if verbose:
        print(f"循环比特 {qubit} · 网关 {base} · 最多 {max_rounds} 轮"
              + (" · dry-run（只算不跑）" if dry_run else ""))

    stalled = 0
    previous_state = ""
    for index in range(1, max_rounds + 1):
        state_resp = read_state(base, qubit, timeout)
        before = state_name(state_resp)
        route = post_json(base, "/api/quantum/qubit/route", {"name": qubit}, timeout=timeout)
        plan = plan_round(
            route,
            before,
            qubit=qubit,
            decide_fn=decide_fn,
            decide_edge=decide_edge,
            decide_params=decide_params,
            strict_params=strict_params,
        )
        if verbose:
            _print_plan(plan, index, max_rounds, qubit)

        if plan["kind"] == KIND_DONE:
            print(f"\n[完成] {qubit} 已经是 {TERMINAL_STATE}，标定结束。")
            return 0
        if plan["kind"] != KIND_EXECUTE:
            print(f"\n[停下] kind={plan['kind']} → exit {exit_code_for(plan['kind'])}")
            return exit_code_for(plan["kind"])

        result = _execute_round(
            base,
            qubit,
            plan,
            ttl_s=ttl_s,
            timeout=timeout,
            run_timeout=run_timeout,
            dry_run=dry_run,
        )
        if dry_run:
            print("[dry-run] 到此为止：确认无误后去掉 --dry-run 再跑")
            return 0

        after_resp = read_state(base, qubit, timeout)
        after = state_name(after_resp)
        print(f"[看板] {before} → {after}" + ("（未推进）" if after == before else ""))
        if verbose:
            print("       刷新 http://localhost:8081/control 看 ① 状态转移 ② QWF 流程图 ③ 本次运行图")

        if after == before:
            stalled += 1
            if stalled >= MAX_STALLED_ROUNDS:
                print(f"\n[停下] 连续 {stalled} 轮没有推进（模拟后端经常出现「跑了但指标没被采纳」）→ exit 3")
                return exit_code_for(KIND_NEEDS_DECISION)
        else:
            stalled = 0
            previous_state = after

        if interval and index < max_rounds:
            print(f"[停顿] {interval:.0f} 秒后再问下一轮")
            time.sleep(interval)

    print(f"\n[停下] 到了 {max_rounds} 轮上限，当前 {previous_state or state_name(read_state(base, qubit, timeout))}")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="状态驱动的测控循环（按 route() 判定选下一步）")
    parser.add_argument("--qubit", default="q0", help="比特名（默认 q0）")
    parser.add_argument("--max-rounds", type=int, default=8, help="最多走几轮（默认 8）")
    parser.add_argument("--interval", type=float, default=0.0, help="两轮之间停顿秒数（现场演示建议 15）")
    parser.add_argument("--ttl", type=float, default=DEFAULT_TTL_S, help="演练证据有效期（秒，默认 8h）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次接口调用上限（秒）")
    parser.add_argument("--run-timeout", type=float, default=600.0, help="等一次运行结束的上限（秒，默认 600）")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"网关地址（默认 {DEFAULT_BASE}）")
    parser.add_argument("--decide-fn", default=None, help="G3 时人指定的 fn（必须在该轮 allowed_actions 内）")
    parser.add_argument("--decide-edge", default=None, help="G3 时人指定的边 id（如 unknown->freq_locked）")
    parser.add_argument("--decide-params", default=None, help="人指定的参数 JSON，如 '{\"freq\": [4.0, 3.0, -0.002]}'")
    parser.add_argument("--strict-params", action="store_true", help="缺真机必需参数时停下而不是继续")
    parser.add_argument("--dry-run", action="store_true", help="只算一轮并打印将提交的 QWF，不真的跑")
    parser.add_argument("--reset", action="store_true", help="开始前清空演练证据")
    parser.add_argument("--json", action="store_true", help="结束时打印一份 JSON 摘要")
    args = parser.parse_args(argv)

    params: Optional[Dict[str, Any]] = None
    if args.decide_params:
        try:
            params = json.loads(args.decide_params)
        except json.JSONDecodeError as exc:
            print(f"--decide-params 不是合法 JSON：{exc}", file=sys.stderr)
            return 1
        if not isinstance(params, dict):
            print("--decide-params 必须是 JSON 对象", file=sys.stderr)
            return 1

    try:
        code = run_loop(
            args.base,
            args.qubit,
            max_rounds=max(1, args.max_rounds),
            interval=max(0.0, args.interval),
            ttl_s=args.ttl,
            timeout=args.timeout,
            run_timeout=args.run_timeout,
            decide_fn=args.decide_fn,
            decide_edge=args.decide_edge,
            decide_params=params,
            strict_params=args.strict_params,
            dry_run=args.dry_run,
            reset=args.reset,
        )
    except RuntimeError as exc:
        print(f"\n[服务不可用] {exc}", file=sys.stderr)
        print("检查 :3002（网关）/ :3003（quantum）/ :3008（workflow）是否在跑；这三个由人启动。", file=sys.stderr)
        return EXIT_SERVICE_DOWN
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130

    if args.json:
        print(json.dumps({"qubit": args.qubit, "exit": code}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

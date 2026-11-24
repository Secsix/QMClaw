#!/usr/bin/env python3
"""tools/demo_control_console.py — 测控控制台（/control）演示剧本（BACKLOG O8）。

一条命令把「状态转移 → QWF 流程图变化 → 每次实验的图」演出来：

  1. 按阶段提交一份 QWF（6 个阶段对齐状态机链路），workflow_service 真的跑实验；
  2. 把这次运行的 metrics 换算成状态机认的证据键（口径写在 ``metrics_to_evidence``）；
  3. 通过 ``POST /api/quantum/qubit/evidence`` 写进「演练证据」通道（source=manual，带 TTL）；
  4. 立刻 ``POST /api/quantum/qubit/state`` 打印「状态 A → 状态 B」，并把图像路径打出来。

为什么不是「跑完实验状态自己就动」：模拟后端（qiskit / offline_sim）产出的 metrics 键
（``T1`` 秒 / ``separation`` / ``SNR`` …）与状态机认的键（``contrast`` / ``T1_us`` / …）不是一套，
而且证据链要求「成功且带 dataset_ids」，模拟实验两条都不满足 —— 所以状态永远停在 unknown。
演练证据就是补这最后一公里，**真实台账证据仍然优先**（见 state_evidence.merge_drill_evidence）。

用法::

    python tools/demo_control_console.py --qubit q0             # 全流程
    python tools/demo_control_console.py --qubit q0 --stage 3   # 只演第 3 阶段
    python tools/demo_control_console.py --reset                # 清空演练证据
    python tools/demo_control_console.py --mode script          # 不依赖后端实验是否可跑

红线：本脚本只写「演练证据」与提交 QWF，不改任何配置、不碰 vendor、不开关硬件写闸门。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_BASE = "http://localhost:3002"
DEFAULT_TTL_S = 8 * 3600.0
DEFAULT_TIMEOUT = 180.0

#: 状态机认的证据键（`services/common/state_evidence.py` 的 METRIC_KEYS）
#: 与模拟后端实际产出的键的对应关系，写在这里便于评审核对。
METRIC_ALIASES = {
    "contrast": ("contrast", "separation", "SNR"),
    "t1_us": ("t1_us", "T1_us", "T1"),
    "t2_star_us": ("t2_star_us", "T2_us", "T2"),
    "xeb_gate_fidelity": ("xeb_gate_fidelity", "gate_fidelity"),
}


# ── HTTP ──────────────────────────────────────────────────────────────────


def _request(url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也可能带 JSON 说明
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{url} → HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} 连不上：{exc.reason}") from exc
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise RuntimeError(f"{url} 返回的不是 JSON：{body[:300]}")


def post_json(base: str, path: str, payload: Dict[str, Any], timeout: float = 30.0) -> Any:
    return _request(base.rstrip("/") + path, payload, timeout=timeout)


def get_json(base: str, path: str, timeout: float = 30.0) -> Any:
    return _request(base.rstrip("/") + path, None, timeout=timeout)


# ── 阶段剧本（QWF 文本 + 证据） ─────────────────────────────────────────────

QWF_TEMPLATES: Dict[int, str] = {
    1: """# 演示第 1 阶段：扫频找 f10
workflow demo01_spectroscopy {
  meta {
    name        "demo01_spectroscopy"
    description "扫频找 f10（状态机第一步：freq_locked）"
    tags        ["demo", "spectroscopy"]
  }
  env {
    qubit "__QUBIT__"
  }
  budget {
    max_wall     5m
    max_writes   2
    max_datasets 2
  }
  measure as s1 {
    fn      sq.spectroscopy
    qubit   $env.qubit
    timeout 120
    note    "演示：扫频，找到 f10 才算频率锁定"
  }
  say "spectroscopy 完成：数据集 ${s1.dataset}"
}
""",
    2: """# 演示第 2 阶段：幅度扫描标定 pi 脉冲
workflow demo02_piamp {
  meta {
    name        "demo02_piamp"
    description "幅度扫描标定 π 脉冲（amp_calibrated）"
    tags        ["demo", "piamp"]
  }
  env {
    qubit "__QUBIT__"
  }
  budget {
    max_wall     8m
    max_writes   2
    max_datasets 2
  }
  measure as s2 {
    fn      sq.piamp
    qubit   $env.qubit
    timeout 300
    note    "演示：幅度扫描，最优幅度即 π 脉冲幅度"
  }
  say "piamp 完成：数据集 ${s2.dataset}"
}
""",
    3: """# 演示第 3 阶段：iqraw 测态区分度（contrast）
workflow demo03_iqraw {
  meta {
    name        "demo03_iqraw"
    description "iqraw 测态区分度（state_discriminated）"
    tags        ["demo", "iqraw", "contrast"]
  }
  env {
    qubit "__QUBIT__"
  }
  budget {
    max_wall     5m
    max_writes   2
    max_datasets 2
  }
  measure as c3 {
    fn      sq.iqraw
    qubit   $env.qubit
    timeout 120
    note    "演示：只重写 discriminator，不动 f10 / 幅度"
  }
  read c3 as r3 { kind IQraw  threshold 0.4 }
  say "contrast=${r3.contrast}（数据集 ${c3.dataset}）"
}
""",
    4: """# 演示第 4 阶段：T1（相干时间的一半证据）
workflow demo04_t1 {
  meta {
    name        "demo04_t1"
    description "测 T1（coherence_measured 的一半）"
    tags        ["demo", "t1"]
  }
  env {
    qubit "__QUBIT__"
  }
  budget {
    max_wall     10m
    max_writes   2
    max_datasets 2
  }
  measure as s4 {
    fn      sq.t1
    qubit   $env.qubit
    timeout 300
    note    "演示：T1 必须固定 zpa 才是 1D 时间扫描"
  }
  say "T1 完成：数据集 ${s4.dataset}"
}
""",
    5: """# 演示第 5 阶段：Ramsey 测 T2*
workflow demo05_ramsey {
  meta {
    name        "demo05_ramsey"
    description "Ramsey 测 T2*（与 T1 一起达标 → coherence_measured）"
    tags        ["demo", "ramsey", "t2"]
  }
  env {
    qubit "__QUBIT__"
  }
  budget {
    max_wall     10m
    max_writes   2
    max_datasets 2
  }
  measure as s5 {
    fn      sq.ramsey_df
    qubit   $env.qubit
    timeout 300
    note    "演示：Ramsey 自由演化，拟合 T2*"
  }
  say "Ramsey 完成：数据集 ${s5.dataset}"
}
""",
    6: """# 演示第 6 阶段：XEB 门保真度
workflow demo06_xeb {
  meta {
    name        "demo06_xeb"
    description "XEB 门保真度（characterized 的最后一环）"
    tags        ["demo", "xeb"]
  }
  env {
    qubit "__QUBIT__"
  }
  budget {
    max_wall     15m
    max_writes   2
    max_datasets 2
  }
  measure as s6 {
    fn      sq.xeb
    qubit   $env.qubit
    timeout 600
    note    "演示：随机化基准，得到门保真度"
  }
  say "XEB 完成：数据集 ${s6.dataset}"
}
""",
}

#: 每阶段要写入的**演练证据**（`--mode script` 直接用；`run` 模式优先用真实 metrics 换算值）。
STAGE_SCRIPT_EVIDENCE: Dict[int, Dict[str, Any]] = {
    1: {"freq_locked": True},
    2: {"contrast": 0.30},
    3: {"contrast": 0.85},
    4: {"t1_us": 45.0},
    5: {"t2_star_us": 32.0},
    6: {"xeb_gate_fidelity": 0.995},
}

#: 判据阈值来源：`config/lab_criteria.json`（这里只做展示文案，不参与判定）
STAGE_TITLES: Dict[int, str] = {
    1: "扫频找 f10",
    2: "幅度扫描（π 脉冲）",
    3: "iqraw 态区分度",
    4: "T1",
    5: "Ramsey T2*",
    6: "XEB 门保真度",
}


def stage_qwf(stage: int, qubit: str) -> str:
    """取某阶段的 QWF 文本（把 ``__QUBIT__`` 换成真比特）。"""
    text = QWF_TEMPLATES.get(int(stage))
    if text is None:
        raise KeyError(f"未知阶段：{stage}（可用 1..6）")
    return text.replace("__QUBIT__", str(qubit))


# ── 指标 → 证据 换算 ───────────────────────────────────────────────────────


def metrics_to_evidence(metrics: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """把一次运行的 metrics 换算成状态机认的证据键。

    返回 ``(evidence, notes)``：``notes`` 逐条记录「原值 → 证据值（口径）」，
    演示时打印出来，评审能顺着核对，不做隐式换算。
    """
    metrics = dict(metrics or {})
    evidence: Dict[str, Any] = {}
    notes: List[str] = []

    def _num(value: Any) -> Optional[float]:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    # contrast：优先用真正的 contrast，其次把 separation / SNR 折成 0..1 的近似
    contrast = _num(metrics.get("contrast"))
    if contrast is not None:
        evidence["contrast"] = round(contrast, 6)
        notes.append(f"contrast={contrast:g}（直接取自 metrics.contrast）")
    else:
        separation = _num(metrics.get("separation"))
        snr = _num(metrics.get("SNR"))
        if separation is not None:
            value = separation if separation <= 1.0 else min(1.0, separation / 2.0)
            evidence["contrast"] = round(value, 6)
            notes.append(f"separation={separation:g} → contrast={value:.4f}（separation/2 归一化）")
        elif snr is not None:
            value = min(1.0, max(0.0, snr / 20.0))
            evidence["contrast"] = round(value, 6)
            notes.append(f"SNR={snr:g} → contrast={value:.4f}（SNR/20 归一化，仅演示口径）")

    # T1 / T2：模拟后端给秒，状态机认微秒
    t1 = _num(metrics.get("T1_us"))
    if t1 is not None:
        evidence["t1_us"] = round(t1, 6)
        notes.append(f"T1_us={t1:g}（直接取自 metrics.T1_us）")
    else:
        t1_s = _num(metrics.get("T1"))
        if t1_s is not None:
            evidence["t1_us"] = round(t1_s * 1e6, 6)
            notes.append(f"T1={t1_s:g}s → t1_us={t1_s * 1e6:.3f}（秒 ×1e6）")

    t2 = _num(metrics.get("T2_us"))
    if t2 is not None:
        evidence["t2_star_us"] = round(t2, 6)
        notes.append(f"T2_us={t2:g}（直接取自 metrics.T2_us）")
    else:
        t2_s = _num(metrics.get("T2"))
        if t2_s is not None:
            evidence["t2_star_us"] = round(t2_s * 1e6, 6)
            notes.append(f"T2={t2_s:g}s → t2_star_us={t2_s * 1e6:.3f}（秒 ×1e6）")

    fidelity = _num(metrics.get("xeb_gate_fidelity"))
    if fidelity is None:
        fidelity = _num(metrics.get("gate_fidelity"))
        if fidelity is not None:
            notes.append(f"gate_fidelity={fidelity:g} → xeb_gate_fidelity")
    if fidelity is not None:
        evidence["xeb_gate_fidelity"] = round(fidelity, 6)

    # xeb_p：状态机的 characterized 需要 xeb 判据，p 与 gate_fidelity 分别取
    xeb_p = _num(metrics.get("xeb_p"))
    if xeb_p is None:
        xeb_p = _num(metrics.get("p_xeb"))
        if xeb_p is not None:
            notes.append(f"p_xeb={xeb_p:g} → xeb_p")
    if xeb_p is not None:
        evidence["xeb_p"] = round(xeb_p, 6)

    if "freq_locked" in metrics:
        evidence["freq_locked"] = bool(metrics.get("freq_locked"))

    return evidence, notes


def decide_stage_evidence(
    stage: int,
    metrics: Optional[Dict[str, Any]],
    mode: str = "run",
    run_ok: bool = True,
) -> Tuple[Dict[str, Any], List[str], str]:
    """决定本阶段写什么证据。返回 ``(evidence, notes, reason)``。

    ``run`` 模式：优先用这次运行换算出的值；运行时若没有可用数值（模拟后端不支持该函数、
    或节点失败），退回脚本内置的口径值，``reason`` 说明为什么。
    """
    script = dict(STAGE_SCRIPT_EVIDENCE[int(stage)])
    if mode == "script" or not run_ok:
        why = "脚本口径（--mode script）" if mode == "script" else "本次运行没有可用指标，退回脚本口径"
        return script, [f"{k}={v!r}" for k, v in script.items()], why
    derived, notes = metrics_to_evidence(metrics)
    wanted = [k for k in script if k in derived]
    complement = {k: derived[k] for k in wanted}
    if not complement:
        merged = dict(script)
        merged.update(derived)
        return merged, notes + [f"{k}={v!r}" for k, v in script.items()], "本次运行没有状态机认的键，退回脚本口径"
    if stage == 1 and "freq_locked" not in complement:
        complement["freq_locked"] = True
        notes.append("本阶段由「扫频实验成功」推得 freq_locked=True")
    missing = [k for k in script if k not in complement]
    if missing:
        notes.append("运行里缺这些键，用脚本口径补齐：" + ", ".join(missing))
        for key in missing:
            complement[key] = script[key]
    return complement, notes, "本次运行的 metrics 换算"


# ── QWF 提交与轮询 ─────────────────────────────────────────────────────────


def submit_qwf(base: str, text: str, qubit: str, timeout: float) -> Tuple[str, Dict[str, Any]]:
    """parse_qwf → run_ir，返回 ``(workflow_id, ir)``。"""
    parsed = post_json(base, "/api/workflow/parse_qwf", {"text": text}, timeout=timeout)
    if not parsed.get("ok"):
        raise RuntimeError(f"parse_qwf 失败：{parsed.get('error') or parsed}")
    ir = parsed.get("ir") or {}
    started = post_json(
        base,
        "/api/workflow/run_ir",
        {"ir": ir, "context": {"qubit": qubit}},
        timeout=timeout,
    )
    if not started.get("ok"):
        raise RuntimeError(f"run_ir 失败：{started.get('error') or started}")
    workflow_id = started.get("workflowId") or started.get("id") or (ir.get("id") or "")
    return str(workflow_id), ir


def wait_run(base: str, workflow_id: str, timeout: float, poll: float = 2.0) -> Dict[str, Any]:
    """轮询到终态（completed / failed / cancelled），超时抛 TimeoutError。"""
    deadline = time.time() + max(1.0, timeout)
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        last = post_json(base, "/api/workflow/status", {"workflowId": workflow_id})
        status = str(last.get("status") or "").lower()
        if status in ("completed", "failed", "cancelled", "aborted", "error", "needs_human"):
            return last
        time.sleep(poll)
    raise TimeoutError(f"等待运行 {workflow_id} 超时（{timeout:.0f}s），最后状态：{last.get('status')}")


def collect_metrics(run: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """从运行节点里收集 metrics 与图像路径。"""
    metrics: Dict[str, Any] = {}
    plots: List[str] = []
    for node in run.get("nodes") or []:
        result = node.get("result") if isinstance(node.get("result"), dict) else {}
        for key, value in (result.get("metrics") or {}).items():
            if key not in metrics and isinstance(value, (int, float, bool)):
                metrics[key] = value
        path = result.get("plotPath") or result.get("plot_path")
        if path:
            plots.append(f"{node.get('id')}: {path}")
    return metrics, plots


# ── 状态查询与打印 ─────────────────────────────────────────────────────────


def read_state(base: str, qubit: str, timeout: float) -> Dict[str, Any]:
    return post_json(base, "/api/quantum/qubit/state", {"name": qubit}, timeout=timeout)


def state_name(response: Dict[str, Any]) -> str:
    state = response.get("state")
    if isinstance(state, dict):
        return str(state.get("state") or "unknown")
    return str(state or "unknown")


def write_evidence(base: str, qubit: str, values: Dict[str, Any], ttl_s: float, note: str, run_id: str, timeout: float) -> Dict[str, Any]:
    return post_json(
        base,
        "/api/quantum/qubit/evidence",
        {
            "name": qubit,
            "entries": values,
            "source": "manual",
            "ttl_s": ttl_s,
            "note": note,
            "run_id": run_id or None,
        },
        timeout=timeout,
    )


def _fmt_seconds(seconds: float) -> str:
    if seconds <= 0:
        return "永不过期"
    if seconds >= 3600:
        return f"约 {seconds / 3600:.1f} 小时"
    return f"约 {seconds / 60:.0f} 分钟"


# ── 主流程 ────────────────────────────────────────────────────────────────


def run_stage(
    base: str,
    qubit: str,
    stage: int,
    mode: str,
    ttl_s: float,
    timeout: float,
    submit: bool,
) -> Dict[str, Any]:
    title = STAGE_TITLES.get(stage, f"阶段 {stage}")
    print(f"\n=== 阶段 {stage}/6 · {title} ===")
    before = read_state(base, qubit, timeout)
    before_state = state_name(before)
    print(f"[状态] 开始前：{before_state}")

    workflow_id = ""
    run: Dict[str, Any] = {}
    run_ok = False
    if submit:
        text = stage_qwf(stage, qubit)
        try:
            workflow_id, _ir = submit_qwf(base, text, qubit, timeout)
            print(f"[QWF ] 已提交 {workflow_id}（刷新 /control 可看到这份流程图）")
            run = wait_run(base, workflow_id, timeout)
            run_ok = str(run.get("status") or "").lower() in ("completed", "ok", "success")
            print(f"[QWF ] 运行结束：{run.get('status')}")
        except (RuntimeError, TimeoutError) as exc:
            print(f"[QWF ] 提交/运行失败：{exc}")
            print("[QWF ] 继续用脚本口径写演练证据（流程图仍已提交，可刷新 /control 看）")
            run_ok = False
    else:
        print("[QWF ] --no-qwf：跳过提交，只写演练证据")

    metrics, plots = collect_metrics(run)
    evidence, notes, reason = decide_stage_evidence(stage, metrics, mode=mode, run_ok=run_ok)
    print(f"[证据] 来源：{reason}")
    for line in notes:
        print(f"       · {line}")
    if plots:
        for line in plots:
            print(f"[图像] {line}")
    else:
        print("[图像] 本次运行没有 plotPath（模拟后端可能没产图，看运行记录节点）")

    written = write_evidence(
        base,
        qubit,
        evidence,
        ttl_s=ttl_s,
        note=f"演示阶段 {stage}：{title}",
        run_id=workflow_id,
        timeout=timeout,
    )
    if not written.get("success"):
        print(f"[证据] 写入失败：{written.get('error')}")
    else:
        print(f"[证据] 已写入 {written.get('count')} 条（ttl {_fmt_seconds(ttl_s)}）")

    after = read_state(base, qubit, timeout)
    after_state = state_name(after)
    drill = after.get("drill_evidence") or {}
    print(f"[状态] 现在：{before_state} → {after_state}"
          + (f"（其中演练证据 {drill.get('count', 0)} 条：{', '.join(drill.get('keys') or [])}）" if drill.get("count") else ""))
    for conflict in (after.get("conflicts") or [])[:3]:
        print(f"[冲突] {conflict.get('key')}：真实={conflict.get('kept')} 演练={conflict.get('dropped')}（真实优先）")
    return {
        "stage": stage,
        "title": title,
        "workflow_id": workflow_id,
        "run_status": run.get("status"),
        "evidence": evidence,
        "evidence_source": reason,
        "state_before": before_state,
        "state_after": after_state,
        "plots": plots,
        "conflicts": after.get("conflicts") or [],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="测控控制台（/control）演示剧本")
    parser.add_argument("--qubit", default="q0", help="演示比特（默认 q0）")
    parser.add_argument("--stage", default="all", help="1..6 或 all（默认 all）")
    parser.add_argument("--mode", choices=("run", "script"), default="run",
                        help="run=优先用运行 metrics 换算（默认）；script=直接用脚本口径")
    parser.add_argument("--reset", action="store_true", help="开始前清空演练证据")
    parser.add_argument("--ttl", type=float, default=DEFAULT_TTL_S, help="演练证据有效期（秒，默认 8h）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单阶段等待运行的上限（秒）")
    parser.add_argument("--base", default=DEFAULT_BASE, help="网关地址（默认 http://localhost:3002）")
    parser.add_argument("--no-qwf", action="store_true", help="只写演练证据，不提交 QWF")
    parser.add_argument("--json", action="store_true", help="最后打印 JSON 摘要")
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    if args.stage == "all":
        stages = [1, 2, 3, 4, 5, 6]
    else:
        try:
            stages = [int(args.stage)]
        except ValueError:
            print(f"--stage 只能是 1..6 或 all，收到：{args.stage}", file=sys.stderr)
            return 2
        if not 1 <= stages[0] <= 6:
            print(f"--stage 只能是 1..6 或 all，收到：{args.stage}", file=sys.stderr)
            return 2

    print(f"演示比特 {args.qubit} · 网关 {base} · 模式 {args.mode}")
    print("提示：/control 页面每 3 秒轮询，切换阶段时刷新即可看到流程图与实验图变化。")

    try:
        if args.reset:
            cleared = post_json(base, "/api/quantum/qubit/evidence/reset", {"name": args.qubit}, timeout=30)
            print(f"[重置] 已清空 {args.qubit} 的演练证据：{(cleared.get('removed') or [])}")
        results = []
        for stage in stages:
            results.append(
                run_stage(
                    base,
                    args.qubit,
                    stage,
                    mode=args.mode,
                    ttl_s=args.ttl,
                    timeout=args.timeout,
                    submit=not args.no_qwf,
                )
            )
    except RuntimeError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130

    print("\n=== 汇总 ===")
    for item in results:
        print(f"阶段 {item['stage']} {item['title']:<16} {item['state_before']} → {item['state_after']}"
              + (f"  [{item['run_status']}]" if item.get("run_status") else ""))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    print("\n打开 http://localhost:8081/control 查看：① 状态转移图 A(s) 高亮 ② QWF 流程图 ③ 本次运行的实验图")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

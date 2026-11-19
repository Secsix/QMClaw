#!/usr/bin/env python
"""qwf_exp.py —— 用一条命令跑 `qwf_scripts/` 里的 qwf 流程/实验脚本。

用法
----
    python qwf_exp.py --list                       # 列出全部脚本（工作流 + 单项）
    python qwf_exp.py calib_single                 # 只打印计划（默认不跑）
    python qwf_exp.py calib_single --if yes        # 真跑
    python qwf_exp.py calib_single --dry           # 干跑：不碰硬件
    python qwf_exp.py --all --if yes               # 全跑 4 条工作流
    python qwf_exp.py --all-experiments --if yes   # 全跑 13 个单项实验
    python qwf_exp.py t1 swap --if yes             # 一次跑多个

脚本分两层（都在 `qwf_scripts/` 下）：

* `workflows/` —— **主菜**：一条流程跑多个实验，带门槛（assert）、条件分支
  （if）、有界循环（repeat）。qwf 本来就是面向工作流的设计，
  一个实验失败不会把整条流程带塌（每个 measure 都 `onError continue`）。
* `experiments/` —— 单项复跑：一份脚本一个实验，用来隔离失败、快速定位。

走的是 qwf 那条唯一推荐入口（不另开捷径）::

    .qwf → /workflows/parse_qwf → validate → manifest → run_ir
         → task_queue 租约 → quantum_service /execute → 后端策略 → Aer

后端由 quantum_service 按 `fallback_config.json` 的 order 选：
``lqcs`` 真机 → ``qiskit``/Aer → ``offline_sim`` 离线回放
（本机没接真机时自动落到 qiskit）。

退出码：0 = 全部完成；1 = 有失败 / 没匹配到脚本名；2 = 参数错。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "qwf_scripts"
WORKFLOWS_DIR = SCRIPTS_DIR / "workflows"
EXPERIMENTS_DIR = SCRIPTS_DIR / "experiments"
WF_BASE = "http://127.0.0.1:3008"

TERMINAL = ("completed", "failed", "cancelled", "budget_exceeded",
            "needs_human", "quota_exceeded")


# ── 脚本发现 ────────────────────────────────────────────────────────────────
def _qwf_files(folder: Path) -> list[tuple[str, Path]]:
    if not folder.is_dir():
        return []
    return sorted((p.stem, p) for p in folder.glob("*.qwf"))


def workflows() -> list[tuple[str, Path]]:
    """工作流脚本（一条流程多个实验）—— 主菜。"""
    return _qwf_files(WORKFLOWS_DIR)


def experiments() -> list[tuple[str, Path]]:
    """单项实验脚本（一份一个实验）—— 隔离/复跑用。"""
    return _qwf_files(EXPERIMENTS_DIR)


def scripts() -> list[tuple[str, Path]]:
    """全部脚本（工作流在前）。名字在两层之间不重名。"""
    return workflows() + experiments()


def describe(path: Path) -> str:
    """从 .qwf 里抠一句说明（meta.description / 第一个 measure 的 fn）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    desc = re.search(r'description\s+"([^"]*)"', text)
    fn = re.search(r"fn\s+(sq\.\w+)", text)
    n_measure = len(re.findall(r"^\s*measure\b", text, re.M))
    parts = []
    if desc:
        parts.append(desc.group(1))
    if n_measure > 1:
        parts.append(f"{n_measure} 个实验")
    elif fn:
        parts.append(fn.group(1))
    return "  ".join(parts)


def print_list() -> None:
    wfs = workflows()
    exps = experiments()
    if not wfs and not exps:
        print(f"没有脚本（{SCRIPTS_DIR} 不存在或为空）")
        return
    if wfs:
        print(f"工作流（{WORKFLOWS_DIR}，共 {len(wfs)} 条，一条流程多个实验）：\n")
        for name, path in wfs:
            print(f"  {name:<18} {describe(path)}")
    if exps:
        print(f"\n单项实验（{EXPERIMENTS_DIR}，共 {len(exps)} 个，隔离失败用）：\n")
        for name, path in exps:
            print(f"  {name:<18} {describe(path)}")
    print("\n跑法：")
    print("  python qwf_exp.py <名字>                    # 只打印计划")
    print("  python qwf_exp.py <名字> --if yes           # 真跑")
    print("  python qwf_exp.py --all --if yes            # 全跑工作流")
    print("  python qwf_exp.py --all-experiments --if yes  # 全跑单项实验")


# ── HTTP ────────────────────────────────────────────────────────────────────
def post(base: str, path: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def node_of(status: dict, node_id: str) -> dict:
    nodes = status.get("nodes") or []
    if isinstance(nodes, dict):
        return nodes.get(node_id) or {}
    for n in nodes:
        if str(n.get("id")) == node_id:
            return n or {}
    return {}


def _nodes_of(status: dict) -> list[dict]:
    nodes = status.get("nodes") or []
    if isinstance(nodes, dict):
        return [dict(n or {}, id=k) for k, n in nodes.items()]
    return [n or {} for n in nodes]


# ── 跑一个 ──────────────────────────────────────────────────────────────────
def run_one(path: Path, args: argparse.Namespace) -> dict:
    """调 qwf_run.py 跑一份 .qwf，再轮询状态收指标。"""
    cmd = [sys.executable, str(ROOT / "qwf_run.py"), str(path), "--base", args.base]
    if args.dry:
        cmd += ["--dry"]
    elif args.confirm == "yes":
        cmd += ["--if", "yes"]

    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    err = proc.stderr or ""

    rec: dict = {"name": path.stem, "file": path.name, "rc": proc.returncode,
                 "wall_s": round(time.time() - started, 1)}
    if args.verbose:
        print(out)

    m = re.search(r"workflowId=(\S+)", out)
    wid = m.group(1) if m else ""
    rec["workflowId"] = wid
    if not wid:
        # 没拿到 workflowId：要么只是打印了计划（默认模式），要么启动就挂了
        rec["status"] = "planned" if args.confirm != "yes" and not args.dry else "no_workflow"
        rec["error"] = (err or out)[-400:] if rec["status"] == "no_workflow" else ""
        return rec

    status = ""
    st: dict = {}
    for _ in range(max(1, args.poll * 2)):
        try:
            st = post(args.base, "/workflows/status", {"workflowId": wid})
        except (urllib.error.URLError, OSError) as exc:
            rec["error"] = f"查状态失败：{exc}"
            break
        status = st.get("status") or ""
        if status in TERMINAL:
            break
        time.sleep(0.5)

    # ── 收节点：工作流里可能有十几个 measure + 若干门槛 ──────────────────
    per_node: dict = {}
    flat_metrics: dict = {}
    gates: dict = {}
    says: list = []
    backends: list = []
    for node in _nodes_of(st):
        nid = str(node.get("id") or "")
        res = node.get("result") or {}
        m = res.get("metrics") or {}
        if m:
            per_node[nid] = m
            for k, v in m.items():
                flat_metrics.setdefault(k, v)
        if res.get("passed") is not None:
            gates[nid] = {"passed": bool(res.get("passed")),
                          "value": res.get("value"),
                          "operator": res.get("operator"),
                          "threshold": res.get("threshold"),
                          "metric": res.get("metric")}
        out_text = (res.get("stdout") or "").strip()
        if out_text and res.get("backend"):
            backends.append(res.get("backend"))
        if nid and node.get("type") in ("print", "notify"):
            if out_text:
                says.append(out_text)
        if node.get("status") == "failed":
            rec.setdefault("failed_nodes", []).append(
                {"id": nid, "error": node.get("error") or res.get("error")})

    first = node_of(st, "c1")
    fres = first.get("result") or {}
    fdata = fres.get("data") or {}
    rec.update({
        "status": status or "unknown",
        "outcome": (st.get("outcome") or {}).get("status"),
        "backend": fres.get("backend"),
        "backends": sorted(set(backends)),
        "approximation": fres.get("approximation"),
        "stdout": (fres.get("stdout") or "").strip(),
        "metrics": flat_metrics,
        "node_metrics": per_node,
        "gates": gates,
        "says": says,
        "points": len(fdata.get("x") or []),
        "elapsed_ms": fres.get("elapsed_ms"),
        "shots": fres.get("shots"),
        "seed": fres.get("seed"),
        "error": first.get("error") or fres.get("error"),
    })
    return rec


def print_row(rec: dict) -> None:
    metrics = rec.get("metrics") or {}
    ms = "  ".join(f"{k}={v}" for k, v in metrics.items())
    print(f"[{rec.get('status')}] {rec['name']:<18} {rec.get('backend') or '-':<8} "
          f"{rec.get('approximation') or '-':<10} {rec.get('wall_s') or 0:>6}s  {ms}",
          flush=True)
    gates = rec.get("gates") or {}
    if gates:
        summary = "  ".join(
            f"{g.get('metric')}{g.get('operator')}{g.get('threshold')}={'PASS' if g.get('passed') else 'FAIL'}"
            for g in gates.values())
        print(f"    门槛: {summary}")
    for s in rec.get("says") or []:
        print(f"    | {s.splitlines()[-1][:160]}")
    for f in rec.get("failed_nodes") or []:
        print(f"    ! 节点 {f.get('id')} 失败：{str(f.get('error'))[:160]}")
    if rec.get("error"):
        print(f"    ! {str(rec['error'])[:200]}")


# ── 入口 ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="跑 qwf_scripts/ 里的工作流 / 单项实验脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="脚本名（见 --list）")
    ap.add_argument("--all", action="store_true", help="全跑工作流（默认范围）")
    ap.add_argument("--all-experiments", action="store_true", help="全跑单项实验脚本")
    ap.add_argument("--list", action="store_true", help="列出全部脚本")
    ap.add_argument("--if", dest="confirm", default="",
                    help="真跑要 --if yes（会驱动硬件/模拟器）")
    ap.add_argument("--dry", action="store_true", help="干跑：不碰硬件")
    ap.add_argument("--base", default=WF_BASE, help=f"workflow_service 地址（默认 {WF_BASE}）")
    ap.add_argument("--poll", type=int, default=300, help="轮询秒数（默认 300）")
    ap.add_argument("--out", default="", help="把汇总写到这个 json")
    ap.add_argument("-v", "--verbose", action="store_true", help="把 qwf_run 的原始输出也打出来")
    args = ap.parse_args(argv)

    if args.list:
        print_list()
        return 0

    items = scripts()
    if not items:
        print(f"没有脚本（{SCRIPTS_DIR} 不存在或为空）")
        return 2

    if args.all_experiments:
        chosen = experiments()
        if not chosen:
            print(f"没有单项实验脚本（{EXPERIMENTS_DIR}）")
            return 2
    elif args.all:
        chosen = workflows()
        if not chosen:
            print(f"没有工作流脚本（{WORKFLOWS_DIR}）")
            return 2
    elif args.names:
        known = dict(items)
        chosen = []
        for n in args.names:
            hit = known.get(n)
            if hit is None:
                print(f"没有这个脚本：{n}（可用：{', '.join(known)}）")
                return 2
            chosen.append((n, hit))
    else:
        print_list()
        print("\n（没给名字；加 --all 全跑工作流，或给一个名字）")
        return 2

    if not args.dry and args.confirm != "yes":
        print("默认只打印计划（不跑）。真跑加 `--if yes`；干跑加 `--dry`。\n")

    rows = []
    for name, path in chosen:
        try:
            rec = run_one(path, args)
        except (urllib.error.URLError, OSError) as exc:
            rec = {"name": name, "file": path.name, "status": "unreachable",
                   "error": f"连不上 {args.base}：{exc}", "metrics": {}}
            print(f"[unreachable] {name:<18} 连不上 {args.base}")
            print(f"    ! workflow_service 没起来？（服务启停由人控制）")
            rows.append(rec)
            continue
        rows.append(rec)
        print_row(rec)

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n汇总 -> {outp}")

    done = [r for r in rows if r.get("status") == "completed"]
    planned = [r for r in rows if r.get("status") == "planned"]
    if planned and not done:
        print(f"\n已打印 {len(planned)} 份计划（未跑）。真跑加 `--if yes`。")
        return 0
    print(f"\n完成 {len(done)}/{len(rows)}")
    return 0 if len(done) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

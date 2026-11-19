#!/usr/bin/env python
"""qwf_exp.py —— 用一条命令跑 `qwf_scripts/` 里的测控实验脚本。

用法
----
    python qwf_exp.py --list                  # 列出全部实验脚本
    python qwf_exp.py t1                      # 只打印计划（默认不跑）
    python qwf_exp.py t1 --if yes             # 真跑
    python qwf_exp.py t1 --dry                # 干跑：不碰硬件
    python qwf_exp.py --all --if yes          # 全跑 13 个
    python qwf_exp.py t1 swap --if yes        # 一次跑多个

走的是 qwf 那条唯一推荐入口（不另开捷径）::

    .qwf → /workflows/parse_qwf → validate → manifest → run_ir
         → task_queue 租约 → quantum_service /execute → 后端策略 → Aer

后端由 quantum_service 按 `fallback_config.json` 的 order 选：
``lqcs`` 真机 → ``qiskit``/Aer → ``offline_sim`` 离线回放
（本机没接真机时自动落到 qiskit）。

脚本本体在 `qwf_scripts/*.qwf`（每份一个实验：一条 measure 失败会 Halt 掉整条
流程，合成一份的话第一个失败就把后面的全带塌了）。参数口径见
`qwf_scripts/README.md`。

退出码：0 = 全部完成；1 = 有失败 / 没匹配到实验名；2 = 参数错。
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
WF_BASE = "http://127.0.0.1:3008"

TERMINAL = ("completed", "failed", "cancelled", "budget_exceeded",
            "needs_human", "quota_exceeded")


# ── 脚本发现 ────────────────────────────────────────────────────────────────
def scripts() -> list[tuple[str, Path]]:
    """列出 `qwf_scripts/` 下所有 .qwf（按文件名排序）。"""
    if not SCRIPTS_DIR.is_dir():
        return []
    return sorted((p.stem, p) for p in SCRIPTS_DIR.glob("*.qwf"))


def describe(path: Path) -> str:
    """从 .qwf 里抠一句说明（meta.description / measure 的 fn）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    desc = re.search(r'description\s+"([^"]*)"', text)
    fn = re.search(r"fn\s+(sq\.\w+)", text)
    parts = []
    if desc:
        parts.append(desc.group(1))
    if fn:
        parts.append(fn.group(1))
    return "  ".join(parts)


def print_list() -> None:
    items = scripts()
    if not items:
        print(f"没有实验脚本（{SCRIPTS_DIR} 不存在或为空）")
        return
    print(f"实验脚本（{SCRIPTS_DIR}，共 {len(items)} 个）：\n")
    for name, path in items:
        print(f"  {name:<16} {describe(path)}")
    print("\n跑法：")
    print("  python qwf_exp.py <名字>            # 只打印计划")
    print("  python qwf_exp.py <名字> --if yes   # 真跑")
    print("  python qwf_exp.py --all --if yes    # 全跑")


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

    node = node_of(st, "c1")
    res = node.get("result") or {}
    data = res.get("data") or {}
    rec.update({
        "status": status or "unknown",
        "outcome": (st.get("outcome") or {}).get("status"),
        "backend": res.get("backend"),
        "approximation": res.get("approximation"),
        "stdout": (res.get("stdout") or "").strip(),
        "metrics": res.get("metrics"),
        "points": len(data.get("x") or []),
        "elapsed_ms": res.get("elapsed_ms"),
        "shots": res.get("shots"),
        "seed": res.get("seed"),
        "error": node.get("error") or res.get("error"),
    })
    return rec


def print_row(rec: dict) -> None:
    metrics = rec.get("metrics") or {}
    ms = "  ".join(f"{k}={v}" for k, v in metrics.items())
    print(f"[{rec.get('status')}] {rec['name']:<16} {rec.get('backend') or '-':<8} "
          f"{rec.get('approximation') or '-':<10} {rec.get('elapsed_ms') or 0:>7}ms  {ms}",
          flush=True)
    if rec.get("error"):
        print(f"    ! {str(rec['error'])[:200]}")


# ── 入口 ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="跑 qwf_scripts/ 里的测控实验脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="实验名（见 --list）")
    ap.add_argument("--all", action="store_true", help="全跑")
    ap.add_argument("--list", action="store_true", help="列出全部实验脚本")
    ap.add_argument("--if", dest="confirm", default="",
                    help="真跑要 --if yes（会驱动硬件/模拟器）")
    ap.add_argument("--dry", action="store_true", help="干跑：不碰硬件")
    ap.add_argument("--base", default=WF_BASE, help=f"workflow_service 地址（默认 {WF_BASE}）")
    ap.add_argument("--poll", type=int, default=120, help="轮询秒数（默认 120）")
    ap.add_argument("--out", default="", help="把汇总写到这个 json")
    ap.add_argument("-v", "--verbose", action="store_true", help="把 qwf_run 的原始输出也打出来")
    args = ap.parse_args(argv)

    if args.list:
        print_list()
        return 0

    items = scripts()
    if not items:
        print(f"没有实验脚本（{SCRIPTS_DIR} 不存在或为空）")
        return 2

    if args.all:
        chosen = items
    elif args.names:
        known = dict(items)
        chosen = []
        for n in args.names:
            hit = known.get(n)
            if hit is None:
                print(f"没有这个实验脚本：{n}（可用：{', '.join(known)}）")
                return 2
            chosen.append((n, hit))
    else:
        print_list()
        print("\n（没给实验名；加 --all 全跑，或给一个名字）")
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
            print(f"[unreachable] {name:<16} 连不上 {args.base}")
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

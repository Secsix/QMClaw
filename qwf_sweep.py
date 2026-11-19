"""qwf_sweep.py — 把 qiskit 后端支持的 13 个实验逐个用 .qwf 跑一遍。

**为什么一个实验一份 .qwf**：一条 measure 失败会 Halt 掉整条流程，
合成一份的话第一个失败就把后面的全带塌了。

**走的仍是 qwf 那条唯一推荐入口**：
    .qwf → /workflows/parse_qwf → validate → manifest → run_ir
         → task_queue 租约 → quantum_service /execute
         → 后端策略（无真机 → qiskit）→ Aer

用法：
    python qwf_sweep.py            # 全跑，输出指标表
    python qwf_sweep.py --only t1 ramsey
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
WF_BASE = "http://127.0.0.1:3008"

# (名称, 函数名, args 文本, timeout 秒, max_wall 文本)
#
# 参数要同时满足两边：
#   * **真机口径**的耗时估算闸门（dsl/backends/lqcs.py 的耗时表）——
#     `sq.t1` 不显式给 zpa/delay 会按厂商默认展开成 3216 点 → 估 2582s >
#     timeout → 下发前直接 Halt。所以 T1 也显式给 `zpa 0.0` + `delay range`。
#   * **qiskit 口径**（qiskit_experiments.py 里各实验 spec 声明的参数名）——
#     不在 spec 里的参数会被 `_qiskit_params_from_code` 过滤掉。
EXPS = [
    ("t1",             "sq.t1",             "points 21\n      t1_us 50\n      zpa 0.0\n      delay range 0 2000 100", 300, "10m"),
    ("ramsey",         "sq.ramsey_df",      "points 41\n      t2_us 20",                      300, "10m"),
    ("piamp",          "sq.piamp",          "points 41\n      amp_max 1.0",                   300, "10m"),
    ("xeb",            "sq.xeb",            "cycles 8\n      sequences 12",                  1800, "40m"),
    ("single_shot",    "sq.single_shot",    "p_readout 0.03",                               300, "10m"),
    ("swap",           "sq.swap",           "error_rate 0.01",                              300, "10m"),
    ("spectroscopy",   "sq.spectroscopy",   "points 51\n      df_hz 5000000",                300, "10m"),
    ("pulsed_spec",    "sq.spectroscopy_iq", "points 51\n      df_hz 5000000",               300, "10m"),
    ("s21",            "sq.s21",            "points 51\n      df_hz 5000000",                300, "10m"),
    ("s21_dis",        "sq.s21_dis",        "points 51\n      df_hz 5000000",                300, "10m"),
    ("iqraw",          "sq.iqraw",          "samples 300\n      p_readout 0.03",              300, "10m"),
    ("allxy",          "sq.allxy",          "error_rate 0.02",                              300, "10m"),
    ("drag_calibrate", "sq.drag_calibrate", "points 41\n      alpha_min -1.0\n      alpha_max 1.0", 300, "10m"),
]

# 双比特线路（2026-11-19 起 `.qwf` 用 `qubits ["q0","q1"]` 显式声明两个比特；
# 之前的 `qubit` 只有一个字段，双比特实验只能标一个名字，线路内部固定 q0/q1）
TWO_QUBIT = {"swap", "drag_calibrate"}


def qwf_text(name, fn, args, timeout, wall) -> str:
    args_block = "\n".join("      " + a for a in args.splitlines())
    # 双比特实验写 `qubits`，单比特写 `qubit`（元数由 config/experiment_configs.json
    # 的 qubits 字段决定，校验阶段就会拦错）
    qubit_line = ('    qubits  ["q0", "q1"]' if name in TWO_QUBIT
                  else "    qubit   $env.qubit")
    return f"""workflow ex_{name} {{
  meta {{
    name        "ex_{name}"
    description "qiskit 后端：{fn}"
    tags        ["qiskit", "{name}"]
  }}

  env {{
    qubit   "q2ld3"
  }}

  budget {{
    max_wall     {wall}
    max_writes   1
    max_datasets 2
  }}

  measure as c1 {{
    fn      {fn}
{qubit_line}
    timeout {timeout}
    args {{
{args_block}
    }}
  }}

  say "{name} 完成"
}}
"""


def post(path, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(WF_BASE + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def node_of(status, node_id):
    nodes = status.get("nodes") or []
    if isinstance(nodes, dict):
        return nodes.get(node_id)
    for n in nodes:
        if str(n.get("id")) == node_id:
            return n
    return None


def run_one(name, fn, args, timeout, wall, outdir):
    path = outdir / f"ex_{name}.qwf"
    path.write_text(qwf_text(name, fn, args, timeout, wall), encoding="utf-8")
    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "qwf_run.py"), str(path), "--if", "yes"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    wid = ""
    m = re.search(r"workflowId=(\S+)", out)
    if m:
        wid = m.group(1)

    rec = {"name": name, "fn": fn, "qubits": 2 if name in TWO_QUBIT else 1,
           "rc": proc.returncode, "workflowId": wid,
           "wall_s": round(time.time() - started, 1)}
    if not wid:
        rec["status"] = "no_workflow"
        rec["error"] = (proc.stderr or out)[-400:]
        return rec

    status = ""
    st = {}
    for _ in range(60):
        st = post("/workflows/status", {"workflowId": wid})
        status = st.get("status") or ""
        if status in ("completed", "failed", "cancelled", "budget_exceeded",
                      "needs_human", "quota_exceeded"):
            break
        time.sleep(1)
    node = node_of(st, "c1") or {}
    res = node.get("result") or {}
    data = res.get("data") or {}
    rec.update({
        "status": status,
        "outcome": (st.get("outcome") or {}).get("status"),
        "backend": res.get("backend"),
        "approximation": res.get("approximation"),
        "stdout": (res.get("stdout") or "").strip(),
        "metrics": res.get("metrics"),
        "plot": res.get("plotPath"),
        "points": len(data.get("x") or []),
        "elapsed_ms": res.get("elapsed_ms"),
        "shots": res.get("shots"),
        "seed": res.get("seed"),
        "error": node.get("error") or res.get("error"),
    })
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[], help="只跑指定实验名")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "qmclaw_qiskit_sweep"))
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    exps = [e for e in EXPS if not args.only or e[0] in args.only]
    if not exps:
        print(f"没有匹配的实验名；可选：{[e[0] for e in EXPS]}")
        return 1

    rows = []
    for name, fn, a, timeout, wall in exps:
        rec = run_one(name, fn, a, timeout, wall, outdir)
        rows.append(rec)
        print(f"[{rec.get('status')}] {name:<15} {fn:<18} "
              f"qubits={rec['qubits']}  {rec.get('stdout', '')[:60]}  "
              f"{(rec.get('metrics') or {})}", flush=True)
        if rec.get("error"):
            print(f"    ! {rec['error'][:200]}")

    (outdir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    ok = sum(1 for r in rows if r.get("status") == "completed")
    print(f"\n完成 {ok}/{len(rows)}；汇总 -> {outdir / 'summary.json'}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

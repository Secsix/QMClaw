"""zpa_qwf_runner.py — 旧版（legacy API）单比特自动标定的「实验执行层」。

为什么单独一个模块
------------------
旧版流水线（`zpa_calibration_pipeline.py`）原本在**本进程**里 `import sq` 并直接
调用 `sq.xxx(qobj, ...)`。改成 `.qwf` 驱动之后，实验不再由本进程调用，而是：

    .qwf 文本 → :3008 /workflows/parse_qwf → /workflows/validate → /workflows/run_ir
             → workflow_service → quantum_service → 厂商库

下发口径见 `services/workflow_service/dsl/backends/lqcs.py` 的 `build_experiment_code()`：
拼出来的是 `sq.<fn>(<qubit>, **args)` —— **正是旧版 legacy 调用形式**（不传 session `s`）。
所以「改用 qwf」不会改变实验语义，只是把调用位置从本进程搬到服务。

两条路线（`Settings.execution_route`）
--------------------------------------
* `qwf`（默认）：实验走 `.qwf`；寄存器写（`bias_z`）走 `:3003/qubit/set_params`
  —— `.qwf` 里**没有写寄存器的节点**（`apply` 只记建议值、不下发硬件），所以 bias
  这一步必须留在驱动里写。
* `direct`：保留旧版行为，在本进程里直调 `sq.<fn>(qobj, **args)`。

参数名一律旧版 legacy 口径
--------------------------
    sq.s21_zpa2d(qobj, zpa=..., freq_span=...)
    sq.spectroscopy(qobj, freq=..., spec_amp=..., update=...)
    sq.iqraw(qobj)
    sq.piamp(qobj, amp=2)
    sq.pidf(qobj, df=0.08)
    sq.ramsey_df(qobj)
    sq.s21_dis(qobj)
    sq.set_pi(qobj, gate='X') / gate='X/2'
    sq.t1(qobj, zpa=selected_bias)

新版 v1.0 的 `piAmp` / `target_gate` / `(s, qobj)` 形式**不在本包使用**；
`step()` 会主动拒绝这些参数名，避免把 `s21zpa/` 那套混进来。
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

WORKFLOW_BASE = os.environ.get("QMCLAW_WORKFLOW_BASE", "http://127.0.0.1:3008")
QUANTUM_BASE = os.environ.get("QMCLAW_QUANTUM_BASE", "http://127.0.0.1:3003")

# 旧版 legacy 实验函数 → 允许的关键字参数（生成 .qwf 前自检用）
LEGACY_EXPERIMENT_ARGS: Dict[str, tuple] = {
    "sq.s21_zpa2d": ("zpa", "freq_span", "update"),
    "sq.spectroscopy": ("freq", "spec_amp", "update"),
    "sq.iqraw": ("update",),
    "sq.piamp": ("amp", "update"),
    "sq.pidf": ("df", "update"),
    "sq.ramsey_df": ("df", "update"),
    "sq.ramsey_t2": ("delay",),
    "sq.s21_dis": ("freq",),
    "sq.set_pi": ("gate",),
    "sq.t1": ("zpa", "delay"),
    "sq.xeb": ("m", "k", "stats"),
}

# 新版 v1.0 的参数名 —— 出现在旧版流水线里就是错
V1_ONLY_ARG_NAMES = {"piAmp", "target_gate", "session", "s"}

# 厂商默认展开量（sq_nodes 实测口径，见 dsl/backends/lqcs.py DEFAULT_SCAN_AXES）
# **必须和引擎的表逐条一致**：workflow_service 用它做运行期预算闸门
# （dsl/executor.py:_estimate_points → backends/lqcs.py:estimate_points），
# 估小了就是 budget_exceeded，实验根本不会下发。
DEFAULT_SCAN_AXES: Dict[str, Dict[str, int]] = {
    "sq.t1": {"delay": 16, "zpa": 201},
    "sq.ramsey_df": {"delay": 51},
    "sq.ramsey_t2": {"delay": 100},
    "sq.s21": {"freq": 20},
    "sq.s21_zpa2d": {"zpa": 16, "freq": 20},
    "sq.s21_dis": {"freq": 40},
    "sq.s21_power2d": {"power": 13, "freq": 20},
    "sq.spectroscopy_auto": {"freq_scan": 33, "zpa_scan": 40},
    "sq.pidf": {"df": 21},
    "sq.pidf21": {"df": 21},
    "sq.time_rabi": {"length": 250},
    "sq.pulse_shape": {"delay": 26},
    "sq.xeb": {"m": 9, "k": 30},
    "sq.iqraw": {"states": 2},
    "sq.test_timing_xyz": {"delay": 1},
    "sq.spectroscopy": {"freq": 1},
}
# 没有隐式扫描轴的函数（单点或由寄存器决定）
NO_SCAN_AXES = {"sq.piamp", "sq.set_pi", "sq.spectroscopy_iq", "sq.impa_gain",
                "sq.pulse_shape_ramsey", "sq.iqcenter"}


def step(alias: str, fn: str, args: Optional[Dict[str, Any]] = None,
         timeout: int = 300, note: str = "") -> Dict[str, Any]:
    """构造一个实验步骤（旧版 legacy 口径）。

    `alias` 必须是合法标识符（写进 `.qwf` 的 `measure as <alias>`）。
    """
    if fn not in LEGACY_EXPERIMENT_ARGS:
        raise ValueError(f"旧版流水线不认这个实验函数：{fn}")
    if not alias or not alias.replace("_", "").isalnum():
        raise ValueError(f"alias 必须是合法标识符：{alias!r}")
    args = dict(args or {})
    for key, value in args.items():
        if key in V1_ONLY_ARG_NAMES:
            raise ValueError(f"{fn} 不接受新版参数名 {key!r}（旧版用 legacy 口径）")
        if key not in LEGACY_EXPERIMENT_ARGS[fn]:
            raise ValueError(f"{fn} 不接受参数 {key!r}（可用：{LEGACY_EXPERIMENT_ARGS[fn]}）")
        if isinstance(value, dict) and "$arange" in value:
            nums = value["$arange"]
            if not (2 <= len(nums) <= 3):
                raise ValueError(f"{fn}.{key} 的 $arange 需要 2 或 3 个数")
            if len(nums) == 3 and float(nums[2]) == 0:
                raise ValueError(f"{fn}.{key} 的 $arange 步长不能为 0")
    if fn == "sq.spectroscopy" and "freq" not in args:
        raise ValueError("sq.spectroscopy 必须显式给 freq（厂商默认会把 freq 当标量 qobj.regs.f10）")
    if fn == "sq.t1" and "zpa" not in args:
        raise ValueError("sq.t1 必须显式给 zpa（不给会展开成 zpa×时间二维扫描）")
    if fn == "sq.s21_zpa2d" and "zpa" not in args:
        raise ValueError("sq.s21_zpa2d 必须显式给 zpa（默认是 16 层）")
    return {"alias": alias, "fn": fn, "args": args, "timeout": int(timeout), "note": note}


# ───────────────────────── .qwf 生成 ─────────────────────────
def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(_fmt(v) for v in value) + "]"
    return f'"{value}"'   # 字符串必须带引号：gate "X/2" 里的 '/' 会被词法拒绝


def _args_block(params: Dict[str, Any]) -> str:
    lines: List[str] = []
    for key, value in params.items():
        if isinstance(value, dict) and "$arange" in value:
            nums = value["$arange"]
            lines.append(f"          {key} range " + " ".join(_fmt(n) for n in nums))
        else:
            lines.append(f"          {key} {_fmt(value)}")
    return "\n".join(lines)


def build_qwf(name: str, description: str, bit: str, session: str,
              budget: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
    """steps: [step(...)] -> 一个 workflow 文本。"""
    if not steps:
        raise ValueError("workflow 至少要一个实验步骤")
    out: List[str] = [
        f"workflow {name} {{",
        "  meta {",
        f'    name        "{name}"',
        f'    description "{description}"',
        '    tags        ["calib", "zpa", "single-qubit", "legacy"]',
        "  }",
        "",
        "  env {",
        f'    qubit   "{bit}"',
        f'    session "{session}"',
        "  }",
        "",
        "  budget {",
        f"    max_wall     {budget['max_wall']}",
        f"    max_writes   {budget['max_writes']}",
        f"    max_datasets {budget['max_datasets']}",
        f"    max_scan_points {budget['max_scan_points']}",
        "  }",
        "",
    ]
    first = steps[0]["alias"]
    out += ["  goal {",
            f'    objective   "{description}"',
            f"    success {{ metric ${first}.dataset  operator >=  value 0 }}",
            "    max_rounds  1",
            '    on_achieved "continue"',
            "  }",
            ""]
    for item in steps:
        out += [f"  measure as {item['alias']} {{",
                f"    fn      {item['fn']}",
                "    qubit   $env.qubit",
                f"    timeout {item['timeout']}",
                f'    note    "{item["note"]}"']
        if item["args"]:
            out += ["    args {", _args_block(item["args"]), "    }"]
        out += ["  }", ""]
    out.append('  say "' + " ".join(f"{s['alias']}=${{{s['alias']}.dataset}}" for s in steps) + '"')
    out.append("}")
    return "\n".join(out) + "\n"


def _arange_points(nums: Iterable[Any]) -> int:
    """`$arange` 会展开多少点（与 np.arange 一致）。"""
    vals = [float(n) for n in nums]
    if len(vals) == 2:
        return max(1, int(math.ceil(abs(vals[1] - vals[0]))))
    if len(vals) >= 3:
        s = abs(vals[2])
        if s <= 0:
            return 1
        return max(1, int(math.ceil(abs(vals[1] - vals[0]) / s - 1e-9)))
    return 1


def _count_points(value: Any) -> int:
    """一个参数值展开成多少点（标量=1；数组=长度；$arange=arange 长度）。"""
    if isinstance(value, dict) and "$arange" in value:
        return _arange_points(value["$arange"])
    if isinstance(value, (list, tuple)):
        return max(1, len(value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 1
    return 1


def estimate_points(item: Dict[str, Any]) -> int:
    """估算一条实验的扫描点数（口径对齐引擎，见 DEFAULT_SCAN_AXES）。"""
    fn, args = item["fn"], item["args"]
    axes = DEFAULT_SCAN_AXES.get(fn)
    if axes is None:
        if fn in NO_SCAN_AXES:
            return 1
        return 1          # 未知函数：引擎也不拦（estimate_points 返回 points=0）
    points = 1
    for axis, default_n in axes.items():
        value = args.get(axis)
        points *= default_n if value is None else _count_points(value)
    return max(1, int(points))


def default_budget(steps: List[Dict[str, Any]], max_wall: str = "60m") -> Dict[str, Any]:
    """预算：max_writes 按**语句条数**计（引擎口径 dsl/validate.py:count_writes）。"""
    return {
        "max_wall": max_wall,
        "max_writes": max(1, len(steps)),
        "max_datasets": max(1, len(steps)),
        "max_scan_points": max(1, max(estimate_points(s) for s in steps)),
    }


# ───────────────────────── HTTP ─────────────────────────
def post(base: str, path: str, payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def get(base: str, path: str, timeout: int = 30) -> Dict[str, Any]:
    with urllib.request.urlopen(base + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


# ───────────────────────── DataVault ─────────────────────────
def dataset_file(root: Path, dataset_id: int) -> Optional[Path]:
    """数据集号 → DataVault 文件（真机目录按日期/设备分子目录，需递归找）。"""
    hits = list(Path(root).rglob(f"{int(dataset_id):05d} - *"))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


# ───────────────────────── 执行器 ─────────────────────────
class DirectRunner:
    """旧版直调路线：在本进程里 `sq.<fn>(qobj, **args)`。"""

    route = "direct"

    def __init__(self, sq: Any, qobj: Any, log: Callable[[str], None] = print):
        self.sq = sq
        self.qobj = qobj
        self.log = log

    def batch(self, steps: List[Dict[str, Any]], name: Optional[str] = None,
              description: Optional[str] = None, budget: Optional[Dict[str, Any]] = None,
              max_wall: str = "60m") -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in steps:
            name = item["fn"].split(".", 1)[1]
            func = getattr(self.sq, name, None)
            if not callable(func):
                raise TypeError(f"sq 缺少 {name}")
            self.log(f"    [direct] {item['fn']} {item['args']}")
            value = func(self.qobj, **item["args"])
            results.append({
                "alias": item["alias"], "fn": item["fn"], "args": item["args"],
                "dataset_id": None, "path": None, "return": value, "error": None,
            })
        return results


class QwfRunner:
    """`.qwf` 路线：实验写成 `.qwf` → workflow_service → quantum_service。

    只读接口：DataVault 数据集、寄存器快照/回滚，都由适配器负责；
    这里只管「把实验发出去、把数据集号收回来」。
    """

    route = "qwf"

    def __init__(self, bit: str, session: str, datavault_root: Path,
                 outdir: Optional[Path] = None,
                 workflow_base: str = WORKFLOW_BASE,
                 dry: bool = False, timeout: int = 7200,
                 log: Callable[[str], None] = print):
        if not session:
            raise ValueError("qwf 路线必须有 session（旧版实验窗口的 session 名）")
        self.bit = bit
        self.session = session
        self.datavault_root = Path(datavault_root)
        self.outdir = Path(outdir) if outdir else None
        self.workflow_base = workflow_base
        self.dry = dry
        self.timeout = timeout
        self.log = log
        self._counter = 0

    # ── 内部：解析 / 校验 / 执行 ──
    def parse_validate(self, text: str) -> Dict[str, Any]:
        parsed = post(self.workflow_base, "/workflows/parse_qwf", {"text": text}, timeout=60)
        if parsed.get("error") or parsed.get("ok") is False:
            raise ValueError(f"parse_qwf 失败：{parsed.get('error')}")
        ir = parsed.get("ir")
        if not ir:
            raise ValueError("parse_qwf 没有返回 IR")
        checked = post(self.workflow_base, "/workflows/validate", {"ir": ir}, timeout=120)
        if checked.get("error"):
            raise ValueError(f"validate 失败：{checked.get('error')}")
        errors = [d for d in (checked.get("diagnostics") or []) if d.get("level") == "error"]
        if errors:
            raise ValueError("validate 报错：" + "; ".join(
                f"{d.get('code')} {d.get('message')}" for d in errors[:5]))
        return ir

    def _run(self, ir: Dict[str, Any], workflow_id: str) -> Dict[str, Any]:
        launch = post(self.workflow_base, "/workflows/run_ir",
                      {"ir": ir, "context": {"qubit": self.bit, "session": self.session},
                       "workflowId": workflow_id, "dryRun": self.dry}, timeout=120)
        if not launch.get("ok"):
            raise RuntimeError(f"run_ir 失败：{launch.get('error')}")
        # 服务端可能另起 id（排队/租约），以它为准
        workflow_id = launch.get("workflowId") or workflow_id
        if launch.get("status") == "running":
            self.log(f"    workflowId={workflow_id} 已下发")
        t0 = time.time()
        while True:
            time.sleep(2)
            state = post(self.workflow_base, "/workflows/status", {"workflowId": workflow_id}, timeout=60)
            status = state.get("status")
            if status in ("completed", "failed", "cancelled", "budget_exceeded",
                          "needs_human", "quota_exceeded", "error"):
                state["elapsed"] = round(time.time() - t0, 1)
                return state
            if time.time() - t0 > self.timeout:
                post(self.workflow_base, "/workflows/cancel", {"workflowId": workflow_id}, timeout=30)
                raise TimeoutError(f"workflow 超时（>{self.timeout}s），已请求取消")

    @staticmethod
    def _node_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for node in state.get("nodes") or []:
            result = node.get("result") if isinstance(node.get("result"), dict) else {}
            out[str(node.get("id"))] = {
                "status": node.get("status"),
                "dataset_id": node.get("dataset_id") or result.get("dataset_id"),
                "error": node.get("error"),
            }
        return out

    # ── 对外：一组实验 → 一条 workflow ──
    def batch(self, steps: List[Dict[str, Any]], name: Optional[str] = None,
              description: Optional[str] = None, budget: Optional[Dict[str, Any]] = None,
              max_wall: str = "60m") -> List[Dict[str, Any]]:
        if not steps:
            raise ValueError("没有实验步骤")
        self._counter += 1
        wf_name = name or f"{self.bit}_zpa_step{self._counter}"
        desc = description or f"{self.bit} 旧版标定流水线步骤 {self._counter}"
        budget = budget or default_budget(steps, max_wall=max_wall)
        text = build_qwf(wf_name, desc, self.bit, self.session, budget, steps)
        if self.outdir:
            self.outdir.mkdir(parents=True, exist_ok=True)
            (self.outdir / f"{wf_name}.qwf").write_text(text, encoding="utf-8")
        if self.dry:
            # 干跑：只做 parse + validate（纯计算，不碰硬件、不下发实验）
            self.parse_validate(text)
            self.log(f"    [qwf:dry] {wf_name}：{len(steps)} 步已通过 parse/validate，未下发")
            return [{"alias": s["alias"], "fn": s["fn"], "args": s["args"],
                     "dataset_id": None, "path": None, "return": None, "error": None}
                    for s in steps]
        ir = self.parse_validate(text)
        self.log(f"    [qwf] {wf_name}：{len(steps)} 步下发（budget={budget}）")
        workflow_id = f"wf_{wf_name}_{int(time.time())}"
        state = self._run(ir, workflow_id)
        self.log(f"    [qwf] {wf_name} → {state.get('status')}（{state.get('elapsed')}s）")
        nodes = self._node_map(state)
        results: List[Dict[str, Any]] = []
        for item in steps:
            node = nodes.get(item["alias"], {})
            dataset_id = node.get("dataset_id")
            path = None
            if dataset_id not in (None, ""):
                path = dataset_file(self.datavault_root, int(dataset_id))
            results.append({
                "alias": item["alias"], "fn": item["fn"], "args": item["args"],
                "dataset_id": dataset_id, "path": str(path) if path else None,
                "return": None, "error": node.get("error"),
                "node_status": node.get("status"),
            })
        if state.get("status") != "completed":
            failed = [r["alias"] for r in results if r.get("error")]
            raise RuntimeError(
                f"workflow {wf_name} 状态 {state.get('status')}"
                + (f"，失败节点 {failed}" if failed else "")
                + (f"：{state.get('error')}" if state.get("error") else ""))
        return results

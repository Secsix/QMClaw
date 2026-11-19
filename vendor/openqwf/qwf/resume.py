"""dsl.resume - v2 的崩溃恢复（F3）。

v1 声明的是"没有 mid-run resume：崩溃后整流程重跑"。v2 要补上这一段——
但要补对，得先想清楚三件事：

1. **恢复的粒度到哪儿？**
   执行器的语句是嵌套的（repeat/if 里有子语句），真要精确到"从第几条语句继续"
   就得把执行游标一起持久化，而游标在嵌套结构里不是单调的（repeat 每轮都会
   重新绑定同名变量）。v1 的实现选**顶层语句边界**做恢复点：顶层 pipeline 里
   已完成的语句整体跳过，嵌套内容随所属顶层语句一起重跑。这样做牺牲了一点
   精度，但换来"恢复点可解释、可复现、可写进 RunRecord"。

2. **额度怎么算？**
   恢复时把已花的额度（writes/datasets/llmCalls/scanPoints 和已用时长）一起
   恢复回来，**已完成的语句不再重跑 ⇒ 不重复扣**。checkpoint 记的是"上一条
   顶层语句边界"的状态，所以崩溃点所在的那条语句在崩溃那轮**没有**被记进
   checkpoint，恢复后它整体重跑并计一次 ⇒ 额度总计恰好等于实际执行次数。

3. **"声明 vs 实际"的对账怎么保证？**
   undeclared 对账靠的是从各节点 stdout 里抠出真机实际写过的键（见
   `IrExecutor._collect_observed_writes`）。恢复运行如果拿不到崩溃前那批
   stdout，对账就会漏掉前半程的写入 ⇒ 结论不可信。所以 checkpoint 里必须
   带上**上一轮每个节点的结果**，恢复时塞回新 workflow 的节点上。

checkpoint 的落点：**RunRecord**（`extra["resume"]`）。所以"从 RunRecord 恢复
执行"是天然支持的：崩溃那次 run 的落档里就带着恢复所需的全部状态。
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ir as ir_mod

CHECKPOINT_VERSION = 1


class CrashInjected(Exception):
    """注入的崩溃（测试用）：在指定位置把执行器打断。

    与真机上的进程崩溃不同，它是**可预期**的：抛出前 checkpoint 已经落盘，
    所以恢复路径可以完全离线验证。
    """


class ResumeError(Exception):
    """恢复请求本身不合法（IR 变了 / checkpoint 版本不认识 / 找不到源）。"""


# ── checkpoint 读写 ────────────────────────────────────────────────────────────

def _jsonable(value: Any) -> Any:
    """把不可 JSON 化的值降级成字符串（checkpoint 要能写进 RunRecord）。"""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return str(value)


def build_checkpoint(ex: Any, *, completed_top: int, stmt_count: int) -> Dict[str, Any]:
    """从执行器当前状态生成一份 checkpoint。

    只取**恢复必需**的东西：绑定值、额度计数、审计链、节点结果（供 undeclared
    对账）、顶层完成数。IR 本身不放进来（RunRecord 里已经有了）。
    """
    nodes: Dict[str, Any] = {}
    if getattr(ex, "workflow", None) is not None:
        for name, node in (ex.workflow.nodes or {}).items():
            result = getattr(node, "result", None)
            if result is None:
                continue
            nodes[name] = {
                "result": _jsonable(result),
                "status": getattr(getattr(node, "status", None), "value", None) or str(getattr(node, "status", "")),
            }
    budget = ex.budget
    return {
        "version": CHECKPOINT_VERSION,
        "workflowId": ex.workflow_id,
        "irHash": ir_mod.ir_hash(ex.ir),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "completedTop": int(completed_top),
        "stmtCount": int(stmt_count),
        "bindings": _jsonable({k: v for k, v in (ex.bindings or {}).items()}),
        "audit": _jsonable(list(ex.audit or [])),
        "budget": {
            "writes": budget.writes,
            "datasets": list(budget.datasets),
            "llmCalls": budget.llm_calls,
            "scanPoints": budget.scan_points,
            "maxScanPointsSeen": budget.max_scan_points_seen,
            "elapsed": round(budget.wall(), 3),
        },
        "nodes": nodes,
    }


def save_checkpoint(path: Any, payload: Dict[str, Any]) -> str:
    """把 checkpoint 写成 JSON 文件（原子写：先写临时文件再替换）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(str(tmp), str(target))
    return str(target)


def load_checkpoint(source: Any) -> Dict[str, Any]:
    """从文件路径读 checkpoint。

    支持三种源：
      1. 直接是 checkpoint JSON（顶层有 `version` + `completedTop`）；
      2. 是一份 RunRecord JSON（顶层有 `resume` 字段）；
      3. 是 RunRecord 里落着的 checkpoint 片段。
    """
    path = Path(source)
    if not path.exists():
        raise ResumeError(f"找不到恢复源：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResumeError(f"恢复源读不出来：{path}（{exc}）")
    return extract_checkpoint(data, origin=str(path))


def extract_checkpoint(data: Any, origin: str = "") -> Dict[str, Any]:
    """从任意结构里把 checkpoint 抠出来（RunRecord / checkpoint / 片段）。"""
    if not isinstance(data, dict):
        raise ResumeError(f"恢复源结构不对（{origin or 'inline'}）：不是对象")
    if "completedTop" in data and "version" in data:
        return data
    if isinstance(data.get("resume"), dict):
        return data["resume"]
    raise ResumeError(
        f"恢复源里没有 checkpoint（{origin or 'inline'}）："
        "需要 RunRecord 的 resume 字段或独立 checkpoint 文件")


def find_checkpoint_file(data_dir: Any, run_id: str) -> Optional[Path]:
    """按 run id 找 RunRecord 文件（`run_<id>.json`）。"""
    if not run_id:
        return None
    base = Path(data_dir)
    direct = base / f"{run_id}.json"
    if direct.exists():
        return direct
    for candidate in sorted(base.glob(f"*{run_id}*.json")):
        return candidate
    return None


# ── 恢复 ──────────────────────────────────────────────────────────────────────

def restore(ex: Any, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    """把 checkpoint 灌回执行器，返回一份"恢复说明"（写进 RunRecord / 报告）。

    三件事：
      1. 校验 IR 一致（irHash 不同 ⇒ 拒绝恢复，避免语义错位）；
      2. 恢复绑定、额度计数、审计链；
      3. 把上一轮的节点结果塞回 workflow 节点（undeclared 对账要用）。
    """
    if not isinstance(checkpoint, dict):
        raise ResumeError("checkpoint 不是对象")
    version = checkpoint.get("version")
    if version != CHECKPOINT_VERSION:
        raise ResumeError(
            f"checkpoint 版本不认识：{version!r}（当前只支持 {CHECKPOINT_VERSION}）")

    want_hash = checkpoint.get("irHash")
    got_hash = ir_mod.ir_hash(ex.ir)
    if want_hash and want_hash != got_hash:
        raise ResumeError(
            f"IR 已经变了，拒绝恢复：checkpoint irHash={want_hash}，当前 irHash={got_hash}")

    restored_nodes: List[str] = []
    nodes = checkpoint.get("nodes") or {}
    if getattr(ex, "workflow", None) is not None:
        for name, payload in nodes.items():
            node = ex.workflow.nodes.get(name)
            if node is None:
                continue
            result = (payload or {}).get("result")
            if result is not None:
                node.result = result
                ex.legacy[name] = result
            ns = getattr(ex, "_NS", None)
            completed = getattr(ns, "COMPLETED", None) if ns is not None else None
            if completed is not None:
                node.status = completed
            node.completed_at = time.time()
            restored_nodes.append(name)

    ex.bindings.update(checkpoint.get("bindings") or {})
    ex.audit[:0] = list(checkpoint.get("audit") or [])

    budget_state = checkpoint.get("budget") or {}
    budget = ex.budget
    budget.writes = int(budget_state.get("writes") or 0)
    budget.datasets = list(budget_state.get("datasets") or [])
    budget.llm_calls = int(budget_state.get("llmCalls") or 0)
    budget.scan_points = int(budget_state.get("scanPoints") or 0)
    budget.max_scan_points_seen = int(budget_state.get("maxScanPointsSeen") or 0)
    # 墙钟继续走：把已用时长挪回起点，恢复后的 maxWallSeconds 仍覆盖整段过程
    elapsed = float(budget_state.get("elapsed") or 0.0)
    budget.started = time.time() - elapsed

    ex._resume_skip_top = int(checkpoint.get("completedTop") or 0)

    return {
        "resumedFrom": checkpoint.get("workflowId"),
        "checkpointAt": checkpoint.get("createdAt"),
        "skippedTopStatements": ex._resume_skip_top,
        "restoredBindings": len(ex.bindings),
        "restoredNodes": restored_nodes,
        "restoredUsage": budget_state,
    }


def crash_after_from_env() -> Optional[int]:
    """环境变量 `QWF_CRASH_AFTER_STMT=N`：执行完 N 条顶层语句后注入崩溃。"""
    raw = os.environ.get("QWF_CRASH_AFTER_STMT")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None

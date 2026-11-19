"""最小宿主：QWF 核心需要的宿主类型、数据目录，以及一个可离线跑的最小服务。

R4（2026-11-18）：原 `dsl/executor.py` 直接 `from .. import server` 取
`Workflow` / `WorkflowNode` / `NodeStatus` / `DATA_DIR`，把核心绑死在
QMClaw 服务模块上。迁到独立仓后：

* 这几个类型搬到这里；
* 真实宿主（QMClaw 的 workflow_service）仍可把自己的类型注入 —— 执行器优先用
  `type(svc).__mro__` 里找到的宿主模块，找不到才用这里的兜底实现；
* 这里另附一个**最小 WorkflowService**（`qwf/host.py::WorkflowService`），
  供离线自检/回归用：它实现了执行器真正用到的那几个面
  （`_workflows` / `_cancel_events` / `_executors` / `_execute_node` /
  `_persist_workflow_run` / `_handle_run_ir`），不含 HTTP 路由、队列、票据账本。
"""

import json
import os
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

_SAFE_FN_RE = re.compile(r"^sq\.[a-z][a-z0-9_]*$")
_SAFE_QUBIT_RE = re.compile(r"^[A-Za-z0-9_%\-]+$")
_SAFE_ARGNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 数据目录：可用环境变量覆盖（宿主部署时指向自己的目录）
DATA_DIR = Path(os.environ.get("QWF_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data" / "workflow-runs"))


def _status_text(status) -> str:
    """NodeStatus/字符串 → 小写状态文本。"""
    return str(getattr(status, "value", status) or "").lower()


def _log(msg: str) -> None:
    """极简日志（与宿主同风格，但不依赖宿主的 logging 配置）。"""
    print(f"[qwf.host] {msg}", flush=True)


class NodeStatus(Enum):
    """节点状态（与 QMClaw workflow_service 的枚举取值一致）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    BUDGET_EXCEEDED = "budget_exceeded"
    NEEDS_HUMAN = "needs_human"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass
class WorkflowNode:
    """工作流节点。"""

    id: str
    type: str
    config: Dict[str, Any] = field(default_factory=dict)
    depends: List[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class Workflow:
    """工作流。"""

    id: str
    name: str
    nodes: Dict[str, WorkflowNode] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    context: Dict[str, Any] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class WorkflowService:
    """新仓自带的最小宿主服务（离线可用）。

    与 QMClaw `workflow_service.server.WorkflowService` 的关系：
    只保留 QWF 核心（`qwf/executor.py`）真正调用的那几个方法，
    不含 HTTP 路由、task 队列、票据账本、真机 HTTP 转发。

    用法::

        svc = WorkflowService(port=0)
        svc._executors = {"experiment": my_node_fn}
        report = svc._handle_run_ir({"ir": ir})
    """

    def __init__(self, port: int = 0):
        self.port = port
        self._workflows: Dict[str, Workflow] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._executors: Dict[str, Any] = {}
        self._default_node_timeout = float(os.environ.get("QMCLAW_NODE_TIMEOUT", "300"))
        self._max_workflows_in_memory = int(os.environ.get("QMCLAW_MAX_WORKFLOWS", "50"))
        # 注册表互斥：executor 注册/淘汰与 status 读取可能来自不同线程
        self._workflows_lock = threading.Lock()
        self.data_dir = DATA_DIR
        # 后端/分析/队列微服务地址（环境变量可覆盖；默认与 QMClaw 部署一致）
        self._quantum_service_url = os.environ.get("QWF_QUANTUM_URL", "http://localhost:3003")
        self._analysis_service_url = os.environ.get("QWF_ANALYSIS_URL", "http://localhost:3004")
        self._queue_service_url = os.environ.get("QWF_QUEUE_URL", "")
        # 本地控制类节点执行器（不经过 HTTP），与 QMClaw workflow_service 同名同义
        self._builtin_executors: Dict[str, Any] = {
            "context": self._exec_context,
            "print": self._exec_print,
            "quality_gate": self._exec_quality_gate,
            "adjust_params": self._exec_adjust_params,
            "notify": self._exec_notify,
            "code": self._exec_code,
            "ask": self._exec_ask,
        }

    # ── 落档 ──────────────────────────────────────────────────────────────
    def _save_run(self, run: Dict[str, Any]) -> str:
        """把一次 run 的记录写到数据目录。"""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            path = self.data_dir / f"{run['id']}.json"
            path.write_text(json.dumps(run, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return str(path)
        except Exception as exc:  # noqa: BLE001 — 落档失败不该把流程带崩
            _log(f"save run failed: {exc!r}")
            return ""

    def register_executor(self, node_type: str, executor: callable):
        """注册节点执行器"""
        self._executors[node_type] = executor
        _log(f"Registered executor for: {node_type}")


    def _resolve_vars(self, value: Any, context: Dict[str, Any], workflow: "Workflow" = None) -> Any:
        """解析 `{{name}}` 引用：运行上下文 → 上游节点结果（含 metrics / variables）。"""
        if not isinstance(value, str) or "{{" not in value:
            return value

        def _lookup(name: str) -> Optional[str]:
            src = context or {}
            # 画布导出的模板写作 {{nodes.c1.contrast}}：节点 id 是 context 的键，
            # 与 {{c1.contrast}} 只差一个前缀，这里统一剥掉，避免"展开了但全是空串"。
            if name.startswith("nodes."):
                name = name[len("nodes."):]
            # 带点路径：先按"节点 id.字段"精确取，再退回旧的全上下文模糊匹配
            if "." in name:
                head, _, tail = name.partition(".")
                res = src.get(head)
                if isinstance(res, dict):
                    for bucket in (res, res.get("metrics") or {}, res.get("variables") or {}):
                        if isinstance(bucket, dict) and tail in bucket and not isinstance(bucket[tail], dict):
                            return str(bucket[tail])
            if name in src and not isinstance(src[name], dict):
                return str(src[name])
            for res in src.values():
                if not isinstance(res, dict):
                    continue
                for bucket in (res, res.get("metrics") or {}, res.get("variables") or {}):
                    if isinstance(bucket, dict) and name in bucket and not isinstance(bucket[name], dict):
                        return str(bucket[name])
            return None

        def _repl(match):
            found = _lookup(match.group(1).strip())
            return found if found is not None else ""

        return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", _repl, value)

    def _node_timeout(self, node: "WorkflowNode") -> float:
        """节点超时（秒）：节点 config.timeout > 工作流 settings.nodeTimeout > 默认值。"""
        try:
            if node.config.get("timeout"):
                return float(node.config["timeout"])
        except Exception:
            pass
        workflow = getattr(node, "_workflow", None)
        try:
            if workflow is not None and workflow.settings.get("nodeTimeout"):
                return float(workflow.settings["nodeTimeout"])
        except Exception:
            pass
        return self._default_node_timeout


    def _exec_context(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """context 节点：把 config.variables 合并进运行上下文并解析 {{}}。"""
        variables = {}
        for k, v in (node.config.get("variables") or {}).items():
            variables[k] = self._resolve_vars(v, context)
        for k, v in variables.items():
            context.setdefault(k, v)
        lines = [f"[CONTEXT] {k} = {v}" for k, v in variables.items()]
        return {"status": "completed", "variables": variables, "stdout": "\n".join(lines)}

    def _exec_print(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """print 节点：渲染模板；没有模板时汇总上游节点结果。"""
        template = node.config.get("message") or node.config.get("text") or node.config.get("template")
        if template:
            rendered = self._resolve_vars(str(template), context)
            return {"status": "completed", "stdout": rendered, "message": rendered}

        lines = ["[SUMMARY]"]
        for dep_id in node.depends or []:
            res = context.get(dep_id) or {}
            if not isinstance(res, dict):
                continue
            parts = [f"{dep_id}:"]
            if res.get("dataset_id"):
                parts.append(f"dataset={res['dataset_id']}")
            if res.get("contrast") is not None:
                parts.append(f"contrast={res['contrast']}")
            for mk, mv in (res.get("metrics") or {}).items():
                parts.append(f"{mk}={mv}")
            if res.get("error"):
                parts.append(f"error={res['error']}")
            lines.append("  " + " ".join(parts))
        if len(lines) == 1:
            lines.append("  (no upstream results)")
        return {"status": "completed", "stdout": "\n".join(lines)}

    def _exec_quality_gate(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """quality_gate：从上游结果取指标与阈值比较。

        config: {metric: 'contrast', threshold: 0.4, operator: '>=', failWorkflow: true}
        """
        cfg = node.config or {}
        metric = str(cfg.get("metric") or "contrast")
        operator = str(cfg.get("operator") or ">=")
        try:
            threshold = float(cfg.get("threshold", 0.4))
        except (TypeError, ValueError):
            threshold = 0.4

        value = cfg.get("value")
        if value is None:
            candidates = list(node.depends or []) + [k for k in context if k not in (node.depends or [])]
            for dep_id in candidates:
                res = context.get(dep_id)
                if not isinstance(res, dict):
                    continue
                for bucket in (res, res.get("metrics") or {}):
                    if isinstance(bucket, dict) and bucket.get(metric) is not None:
                        value = bucket[metric]
                        break
                if value is not None:
                    break
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                value = None
        if value is None:
            return {"status": "failed", "error": f"quality_gate: metric '{metric}' not found upstream"}

        ops = {
            ">=": value >= threshold,
            ">": value > threshold,
            "<=": value <= threshold,
            "<": value < threshold,
            "==": abs(float(value) - threshold) < 1e-9,
        }
        passed = bool(ops.get(operator, value >= threshold))
        out = {
            "status": "completed",
            "passed": passed,
            "value": value,
            "threshold": threshold,
            "operator": operator,
            "metric": metric,
            "metrics": {metric: value, "threshold": threshold, "passed": passed},
            "stdout": f"[QUALITY GATE] {metric}={value} {operator} {threshold} -> {'PASS' if passed else 'FAIL'}",
        }
        if not passed and cfg.get("failWorkflow", True):
            out["status"] = "failed"
            out["error"] = f"quality gate failed: {metric}={value} {operator} {threshold}"
        return out

    def _exec_adjust_params(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """adjust_params：暂不下发硬件，只记录建议值。"""
        changes = {k: self._resolve_vars(v, context) for k, v in (node.config.get("params") or {}).items()}
        lines = [f"[ADJUST] {k} -> {v}" for k, v in changes.items()] or ["[ADJUST] no params"]
        return {"status": "completed", "params": changes, "stdout": "\n".join(lines)}

    def _exec_ask(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """ask 节点：目标歧义澄清 —— 向人提问，把流程停在 needs_human。

        这不是 LLM 问答（那是 `llm` 节点）。`ask` 的语义是"这一步需要人来拍板"，
        因此返回 `status="needs_human"`，由执行器把工作流收成 needs_human，
        再由 outcome 契约告诉上层"还差一个人类的输入"。
        """
        question = str(node.config.get("question") or node.config.get("prompt") or "")
        options = node.config.get("options") or []
        if isinstance(options, str):
            options = [options]
        options = [str(o) for o in options]
        blocking = bool(node.config.get("blocking", True))
        if not question:
            return {
                "status": "failed",
                "error": "ask 节点缺少 question（目标歧义澄清必须写清要问什么）",
            }
        msg = f"[NEEDS_HUMAN] {question}"
        if options:
            msg += "（可选：" + " / ".join(options) + "）"
        _log(msg)
        return {
            "status": "needs_human",
            "needs_human": True,
            "question": question,
            "options": options,
            "blocking": blocking,
            "message": question,
            "stdout": msg,
        }

    def _exec_notify(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        msg = self._resolve_vars(str(node.config.get("message", "")), context)
        _log(f"[notify] {msg}")
        return {"status": "completed", "stdout": f"[NOTIFY] {msg}", "message": msg}

    def _exec_code(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """code 节点：默认拒绝在服务进程里执行任意代码（安全）。"""
        if not node.config.get("allowLocalCode"):
            return {
                "status": "failed",
                "error": "code 节点默认禁用；如确需在服务进程执行，请设置 allowLocalCode=true",
            }
        try:
            local_ns: Dict[str, Any] = {}
            exec(str(node.config.get("code", "")), {"__builtins__": __builtins__, "context": context}, local_ns)
            body = "\n".join(f"{k} = {v}" for k, v in local_ns.items() if not k.startswith("__"))
            return {"status": "completed", "stdout": body}
        except Exception as e:
            return {"status": "failed", "error": f"code node failed: {e}"}


    @staticmethod
    def _classify_result(result: Dict[str, Any]) -> tuple:
        """判断节点结果是成功还是失败。

        以前 `_execute_via_http` 出错时只返回 `{"error": ...}`，节点照样被标记
        COMPLETED，工作流带着错误继续往下跑（实测 piamp 超时后 e3 仍然执行）。
        这里统一把“带 error / status=failed|error|timeout / success=false”判为失败。
        """
        if not isinstance(result, dict):
            return True, None
        err = result.get("error")
        status = str(result.get("status") or "").lower()
        if err:
            return False, str(err)
        if status in ("failed", "error", "timeout", "cancelled"):
            return False, str(result.get("error") or f"node status={status}")
        if result.get("success") is False:
            return False, str(result.get("error") or "success=false")
        return True, None


    def _execute_node(self, workflow: Workflow, node_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个节点（按 `config.retries` / `config.retryDelay` 重试）。"""
        node = workflow.nodes[node_id]
        try:
            retries = max(0, int(node.config.get("retries", 0) or 0))
        except (TypeError, ValueError):
            retries = 0
        try:
            retry_delay = max(0.0, float(node.config.get("retryDelay", 2) or 0))
        except (TypeError, ValueError):
            retry_delay = 2.0
        attempts = 1 + retries
        result: Dict[str, Any] = {}

        for attempt in range(attempts):
            result = self._execute_node_once(workflow, node_id, context)
            if node.status != NodeStatus.FAILED or attempt + 1 >= attempts:
                break
            _log(f"Node {node_id} failed (attempt {attempt + 1}/{attempts}): {node.error}; "
                 f"retrying in {retry_delay}s")
            # 分片睡眠，取消/超时时能及时退出
            cancel_event = self._cancel_events.get(workflow.id)
            slept = 0.0
            while slept < retry_delay:
                if cancel_event is not None and cancel_event.is_set():
                    return result
                time.sleep(min(0.5, retry_delay - slept))
                slept += 0.5
            node.status = NodeStatus.RUNNING
            node.error = None
        return result

    def _execute_node_once(self, workflow: Workflow, node_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个节点（单次尝试）"""
        node = workflow.nodes[node_id]
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()

        _log(f"Executing node {node_id} (type={node.type})")

        try:
            executor = self._executors.get(node.type)
            builtin = self._builtin_executors.get(node.type)
            node._workflow = workflow
            if executor:
                result = executor(node, context)
            elif builtin:
                result = builtin(node, context)
            else:
                result = self._execute_via_http(node, context, workflow=workflow)

            ok, err = self._classify_result(result)
            node.result = result
            cancelled = isinstance(result, dict) and str(result.get("status") or "").lower() == "cancelled"
            needs_human = (isinstance(result, dict)
                           and (result.get("needs_human") is True
                                or str(result.get("status") or "").lower() == "needs_human"))
            if cancelled:
                # 取消不是“失败”：节点标 cancelled，工作流由 cancel_event 收尾
                node.status = NodeStatus.CANCELLED
                node.error = None
            elif needs_human:
                # 等人拍板也不是“失败”：单独一种状态，由 outcome 契约往上冒
                node.status = NodeStatus.NEEDS_HUMAN
                node.error = None
            elif ok:
                node.status = NodeStatus.COMPLETED
            else:
                node.status = NodeStatus.FAILED
                node.error = err
            node.completed_at = time.time()
            context[node_id] = result

            _log(f"Node {node_id} {node.status.value}" + (f": {err}" if err else ""))
            return result

        except Exception as e:
            _log(f"Node {node_id} failed: {e}\n{traceback.format_exc()}")
            node.status = NodeStatus.FAILED
            node.error = str(e)
            node.completed_at = time.time()
            return {"error": str(e)}

    def _persist_workflow_run(self, workflow_id: str, context: Dict[str, Any],
                              extra: Dict[str, Any] = None):
        """将工作流运行结果持久化到磁盘"""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            _log(f"Cannot persist: workflow {workflow_id} not found")
            # D1-③：落档失败必须**看得见**，不能静默 return（调用方会兜底写
            # run-persist-failed-*.json，否则 run 级 JSON 就凭空消失了）。
            return {"ok": False, "error": f"workflow {workflow_id} not found"}

        started_at = workflow.created_at * 1000  # 转换为毫秒
        completed_at = (workflow.completed_at or time.time()) * 1000
        run_id = f"run_{workflow_id}_{int(started_at)}"

        # 构建节点列表
        nodes = []
        for node_id, node in workflow.nodes.items():
            # 从上下文中提取节点输出
            node_output = context.get(node_id, {})
            if isinstance(node_output, dict):
                result = node_output
            else:
                result = {"stdout": str(node_output)}

            nodes.append({
                "nodeId": node_id,
                "nodeType": node.type,
                "status": node.status.value if hasattr(node.status, 'value') else str(node.status),
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(node.started_at or workflow.created_at)),
                "completedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(workflow.completed_at or time.time())),
                "duration": int((node.completed_at - node.started_at) * 1000) if node.started_at and node.completed_at else None,
                "input": {
                    "config": node.config,
                    "resolvedContext": {},
                },
                "output": {
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "error": result.get("error"),
                    "metrics": result.get("metrics"),
                    "plotPath": result.get("plotPath"),
                    "conversation": result.get("conversation"),
                    "recommendations": result.get("recommendations"),
                    "symptom": result.get("symptom"),
                    "reasoning": result.get("reasoning"),
                    "matchedRules": result.get("matchedRules"),
                },
            })

        run = {
            "id": run_id,
            "workflowId": workflow_id,
            "workflowName": workflow.name,
            "status": workflow.status.value if hasattr(workflow.status, 'value') else str(workflow.status),
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(workflow.created_at)),
            "completedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(workflow.completed_at or time.time())),
            "totalDuration": int(completed_at - started_at),
            "context": context,
            "nodes": nodes,
        }
        if extra:
            # DSL 侧审计字段（irHash / ir / usage / statements / dryRun …）
            for key, value in extra.items():
                run.setdefault(key, value)

        self._save_run(run)
        _log(f"Persisted workflow run: {run_id}")
        return {"ok": True, "runId": run_id}

    # ── 新仓侧的入口 ──────────────────────────────────────────────────────
    # ── 适配器钩子（厂商知识住在 adapters/，宿主只做转发）────────────────
    @property
    def _lqcs(self):
        """lqcs 适配器（懒建；每次访问同步 quantum 地址、默认超时与队列地址）。"""
        from adapters.lqcs import LqcsAdapter

        adapter = getattr(self, "_lqcs_adapter", None)
        if adapter is None:
            adapter = LqcsAdapter(logger=_log)
            self._lqcs_adapter = adapter
        adapter.base_url = str(self._quantum_service_url).rstrip("/")
        adapter.default_timeout = float(self._default_node_timeout)
        adapter.queue_url = str(self._queue_service_url or "").rstrip("/")
        return adapter

    def _estimate_scan_points(self, fn, params) -> Optional[Dict[str, Any]]:
        """这次实验会扫多少点（执行器下发前的预算闸门用）；拿不到就不拦。"""
        try:
            est = getattr(self._lqcs, "estimate_points", None)
            return est(fn, params or {}) if callable(est) else None
        except Exception as exc:
            _log(f"[Workflow] estimate_points failed for {fn}: {exc}")
            return None

    def _plan_ir(self, ir: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """编译期成本估算（每步点数/耗时/是否改参数）；失败按"未知、不拦"处理。"""
        try:
            from .plan import plan_ir
            return plan_ir(ir, self._lqcs)
        except Exception as exc:
            _log(f"[Workflow] plan_ir failed: {exc}")
            return None

    def _write_key(self, name: str, path: Optional[List[str]] = None) -> Optional[str]:
        """一次真机写入 → 写入语义表里的键名。"""
        try:
            return self._lqcs.write_key(name, path)
        except Exception:
            return None

    def _drift_threshold(self, key: str) -> Optional[Dict[str, Any]]:
        """这个键被挪动多少算危险漂移（看护表在适配器里）。"""
        try:
            return self._lqcs.drift_threshold(key)
        except Exception:
            return None

    def _cancel_event_for(self, node) -> Optional[threading.Event]:
        workflow = getattr(node, "_workflow", None)
        if workflow is None:
            return None
        return self._cancel_events.get(workflow.id)

    def _abandon_quantum_job(self, job_id: str, base: str) -> None:
        try:
            adapter = self._lqcs
            if base:
                adapter.base_url = str(base).rstrip("/")
            adapter.abandon(job_id)
        except Exception as exc:
            _log(f"[Workflow] abandon {job_id} failed: {exc}")

    def _run_quantum_job(self, node, code: str) -> Dict[str, Any]:
        """以异步 job 方式跑一个实验节点（超时/取消会真正 abandon）。"""
        return self._lqcs.submit_and_wait(
            code,
            self._node_timeout(node),
            self._cancel_event_for(node),
            node_id=getattr(node, "id", "-"),
            kind=getattr(node, "type", ""),
            config=getattr(node, "config", None) or {},
        )

    def _annotate_result(self, node, result: Dict[str, Any]) -> Dict[str, Any]:
        """把注入片段打印的标记解析成结构化字段（实验 dataset_id / 对比度）。"""
        from adapters.lqcs import annotate

        return annotate(getattr(node, "type", ""), getattr(node, "config", None) or {}, result)

    # ── 节点执行：控制类走本地执行器，实验/对比度走适配器 ────────────────
    def _execute_via_http(self, node, context: Dict[str, Any] = None, workflow=None) -> Dict[str, Any]:
        """执行没有本地执行器的节点。

        独立仓只内置两类"真机语义"（experiment / contrast）：它们都要经适配器拼代码
        再走异步 job，是 DSL 自身的核心语义。其余类型（analysis / llm / image_* 等）
        属于部署侧的微服务编排，由使用方通过 `register_executor()` 注入。
        """
        node_type = getattr(node, "type", "")
        config = getattr(node, "config", None) or {}

        if node_type == "experiment":
            raw_fn = str(config.get("fn", "sq.iqraw") or "").strip()
            if not _SAFE_FN_RE.match(raw_fn):
                return {"status": "failed",
                        "error": f"invalid experiment fn {raw_fn!r}: expected 'sq.<snake_case>'"}
            raw_qubit = str(config.get("qubit", "") or "").strip()
            qubit = str(self._resolve_vars(raw_qubit, context or {})).strip()
            if "{{" in raw_qubit and not qubit:
                return {"status": "failed", "error": f"unresolved qubit variable in {raw_qubit!r}"}
            if qubit and not _SAFE_QUBIT_RE.match(qubit):
                return {"status": "failed", "error": f"invalid qubit {qubit!r}: allowed [A-Za-z0-9_%-]"}
            params = {k: self._resolve_vars(v, context or {})
                      for k, v in (config.get("params") or {}).items()}
            # 服务端执行一律不画图：do_plot=True 会拉起 GUI 把微服务卡死
            params.setdefault("do_plot", False)
            # 多比特：`qubits` 是列表（每个元素都要解变量）；`qubit`（单数）
            # 是历史写法，compile 阶段已经把它收敛成第一个比特。
            raw_qubits = config.get("qubits")
            qubits: List[str] = []
            if isinstance(raw_qubits, list):
                for item in raw_qubits:
                    resolved = str(self._resolve_vars(item, context or {})).strip()
                    if resolved and not _SAFE_QUBIT_RE.match(resolved):
                        return {"status": "failed",
                                "error": f"invalid qubit {resolved!r}: allowed [A-Za-z0-9_%-]"}
                    if resolved:
                        qubits.append(resolved)
            from adapters.lqcs import build_experiment_code

            try:
                code = build_experiment_code(raw_fn, qubit, params,
                                             bool(config.get("reportDataset", True)),
                                             qubits=qubits)
            except ValueError as exc:
                return {"status": "failed", "error": str(exc)}
            return self._run_quantum_job(node, code)

        if node_type == "contrast":
            raw_qubit = str(config.get("qubit", "{{qubit}}") or "").strip()
            qubit = str(self._resolve_vars(raw_qubit, context or {})).strip()
            if not qubit or not _SAFE_QUBIT_RE.match(qubit):
                return {"status": "failed", "error": f"invalid qubit for contrast: {qubit!r}"}
            dataset_id = config.get("datasetId")
            if not dataset_id:
                for dep_id in getattr(node, "depends", []) or []:
                    dep_result = (context or {}).get(dep_id) or {}
                    if isinstance(dep_result, dict) and dep_result.get("dataset_id"):
                        dataset_id = dep_result["dataset_id"]
                        break
            if dataset_id not in (None, ""):
                try:
                    dataset_id = int(dataset_id)
                except (TypeError, ValueError):
                    return {"status": "failed", "error": f"invalid datasetId {dataset_id!r}"}
                if dataset_id <= 0:
                    return {"status": "failed", "error": f"invalid datasetId {dataset_id!r}"}
            kind = str(config.get("kind") or "IQraw").strip()
            if not _SAFE_ARGNAME_RE.match(kind):
                return {"status": "failed", "error": f"invalid dataset kind {kind!r}"}
            from adapters.lqcs import build_contrast_code

            return self._run_quantum_job(node, build_contrast_code(dataset_id, qubit, kind))

        return {"status": "failed",
                "error": f"no executor registered for node type {node_type}; "
                         f"register one with WorkflowService.register_executor(...)"}

    # ── 极简 HTTP 路由面（离线/嵌入使用；完整服务由部署侧提供）────────────
    def _status_payload(self, workflow_id: str) -> Dict[str, Any]:
        """查一次 run 的状态（按 statements 还原节点视图）。"""
        cache = getattr(self, "_reports", None) or {}
        report = cache.get(workflow_id)
        if report is None:
            return {"ok": False, "status": "unknown",
                    "error": f"no such workflow {workflow_id}"}
        nodes = [
            {"id": s.get("as"), "type": s.get("op"), "status": s.get("status")}
            for s in (report.get("statements") or []) if isinstance(s, dict)
        ]
        return {"ok": True, "workflowId": workflow_id, "status": report.get("status"),
                "nodes": nodes, "result": report}

    def handle_request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
                       headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """极简路由：`(method, path, body, headers)` → 响应 dict。

        覆盖 .qwf 生态离线使用所需的四类入口（validate / parse_qwf / ir_from_canvas /
        run_ir + status / migrate）。完整部署（Express 网关、鉴权、任务队列）不在这里。
        """
        from .compile import ir_from_canvas
        from .migrate import migrate
        from .textdsl import DSLSyntaxError, parse as parse_qwf
        from .validate import format_diags, validate as dsl_validate

        body = body or {}
        route = "/" + str(path).strip("/")
        try:
            if route == "/workflows/validate":
                normalized, diags = dsl_validate(
                    body.get("ir") or {},
                    vendor_check=bool(body.get("vendorCheck")),
                    vendor_strict=bool(body.get("vendorStrict")),
                )
                errors = [d for d in diags if d.get("level") == "error"]
                return {"ok": not errors, "ir": normalized, "errors": errors,
                        "warnings": [d for d in diags if d.get("level") != "error"],
                        "summary": format_diags(errors)}
            if route == "/workflows/parse_qwf":
                try:
                    ir = parse_qwf(str(body.get("text") or ""))
                except DSLSyntaxError as exc:
                    return {"ok": False, "error": str(exc)}
                return {"ok": True, "ir": ir}
            if route == "/workflows/ir_from_canvas":
                ir = ir_from_canvas(body.get("name") or "canvas", body.get("nodes") or [],
                                    body.get("context") or {}, body.get("settings") or {},
                                    body.get("edges") or [])
                return {"ok": True, "ir": ir}
            if route == "/workflows/migrate":
                return {"ok": True,
                        "ir": migrate(body.get("ir") or {}, int(body.get("targetVersion") or 2))}
            if route == "/workflows/run_ir":
                return self._handle_run_ir(body)
            if route == "/workflows/status":
                return self._status_payload(str(body.get("workflowId") or ""))
            return {"ok": False, "error": f"no route {method} {route}"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def run_ir(self, ir: Dict[str, Any], context: Optional[Dict[str, Any]] = None,
               dry_run: bool = False, dry_provider=None, workflow_id: Optional[str] = None,
               **executor_kwargs: Any) -> Dict[str, Any]:
        """同步执行一份 IR（已校验），返回执行器报告。

        与宿主 `_handle_run_ir` 的差别：宿主是"后台线程 + 立即返回 workflowId"，
        这里**同步跑完**再返回，便于离线自检断言。

        `executor_kwargs` 透传给执行器（`budget_mode` / `resume_from` /
        `crash_after` / `checkpoint_path`）。
        """
        from .executor import run_ir as _run_ir

        report = _run_ir(self, ir, context or {}, dry_run=dry_run,
                         dry_provider=dry_provider, workflow_id=workflow_id,
                         **executor_kwargs)
        self._last_report = report
        return report

    def _evict_old_workflows(self) -> None:
        """内存回收：只保留最近 N 个已结束的工作流（调用方持有 `_workflows_lock`）。"""
        limit = getattr(self, "_max_workflows_in_memory", 0)
        if not limit or limit <= 0 or len(self._workflows) <= limit:
            return
        finished = [
            wf for wf in self._workflows.values()
            if _status_text(wf.status) in ("completed", "failed", "cancelled",
                                           "budget_exceeded", "needs_human")
        ]
        finished.sort(key=lambda w: getattr(w, "created_at", 0) or 0)
        for wf in finished[: max(0, len(self._workflows) - limit)]:
            self._workflows.pop(wf.id, None)
            self._cancel_events.pop(wf.id, None)

    def _handle_run_ir(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`{ir, context, dryRun}` → 校验后同步执行（离线自检用；宿主是异步版）。"""
        from .validate import format_diags, validate as dsl_validate
        from .dryrun import DryRunProvider

        dry_run = bool(data.get("dryRun") or data.get("dry_run"))
        ir = data.get("ir") or {}
        normalized, diags = dsl_validate(ir, require_writes=bool(data.get("requireWrites", not dry_run)))
        errors = [d for d in diags if d.get("level") == "error"]
        if errors:
            return {
                "ok": False,
                "error": errors[0].get("message"),
                "errors": errors,
                "diags": diags,
                "summary": format_diags(diags),
            }
        provider = None
        if dry_run:
            provider = DryRunProvider(mode=str(data.get("dryMode") or "synthetic"))
        report = self.run_ir(normalized, data.get("context") or {}, dry_run=dry_run,
                             dry_provider=provider)
        if not hasattr(self, "_reports"):
            self._reports: Dict[str, Any] = {}
        if report.get("workflowId"):
            self._reports[report["workflowId"]] = report
        return {
            "ok": True,
            "workflowId": report.get("workflowId"),
            "status": report.get("status"),
            "result": report,
        }

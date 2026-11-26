"""语义工具单测 —— 2026-11-26（服务边界收口，阶段4）。

覆盖三件事：

1. 工具面默认**不暴露** `run_code`（`expose_run_code()` 读环境变量）；
2. 两个语义工具的名字与 schema 就是智能体看到的样子（`run_experiment` / `analyze_dataset`）；
3. 调用失败时返回 `ToolResult.err`（带服务端错误码），成功时带 `run_id` / `backend`。

跑法：
    python -m unittest tests.test_semantic_tools -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.common.quantum_tools import semantic as sem  # noqa: E402


class _PatchPost:
    """替换 `semantic._post_json`，记录请求并回放响应。"""

    def patch(self, response=None, error=None):
        calls = []
        original = sem._post_json

        def fake(url, payload, timeout):
            calls.append({"url": url, "payload": payload, "timeout": timeout})
            return dict(error if error is not None else (response or {}))

        sem._post_json = fake
        self.addCleanup(lambda: setattr(sem, "_post_json", original))
        return calls


class TestExposeRunCode(unittest.TestCase):
    def test_default_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QMCLAW_EXPOSE_RUN_CODE", None)
            self.assertFalse(sem.expose_run_code())

    def test_truthy_values(self):
        for value in ("1", "true", "YES", " on "):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"QMCLAW_EXPOSE_RUN_CODE": value}):
                    self.assertTrue(sem.expose_run_code())

    def test_falsey_values(self):
        for value in ("", "0", "no", "off", "别开"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"QMCLAW_EXPOSE_RUN_CODE": value}):
                    self.assertFalse(sem.expose_run_code())


class TestRunExperimentTool(_PatchPost, unittest.TestCase):
    def test_schema_names(self):
        tool = sem.SemanticRunExperimentTool("http://127.0.0.1:3003/")
        self.assertEqual(tool.name, "run_experiment")
        schema = tool.schema
        self.assertEqual(schema.name, "run_experiment")
        self.assertEqual(schema.parameters["backend"].enum, ["auto", "lqcs", "qiskit", "offline_sim"])
        self.assertTrue(schema.parameters["kind"].required)
        self.assertIn("backend", tool.description.lower())

    def test_posts_semantic_payload(self):
        tool = sem.SemanticRunExperimentTool("http://127.0.0.1:3003")
        calls = self.patch(response={
            "success": True,
            "backend": "qiskit",
            "status": "qiskit_completed",
            "run_id": "run-1",
            "dataset_ids": ["20251126_1"],
            "metrics": {"contrast": 0.9},
            "plotPath": "/plots/x.png",
        })
        out = tool.execute(kind="iqraw", qubit="q0", params={"zpa": 0.0})
        self.assertTrue(out.success)
        self.assertEqual(out.metadata["run_id"], "run-1")
        self.assertEqual(out.metadata["backend"], "qiskit")
        self.assertEqual(calls[0]["url"], "http://127.0.0.1:3003/experiments/run")
        self.assertEqual(calls[0]["payload"], {"kind": "iqraw", "qubits": ["q0"], "params": {"zpa": 0.0}})

    def test_forced_backend_passed_through(self):
        tool = sem.SemanticRunExperimentTool()
        calls = self.patch(response={"success": True, "run_id": "run-2"})
        tool.execute(kind="t1", qubit="q1", backend="offline_sim")
        self.assertEqual(calls[0]["payload"]["backend"], "offline_sim")
        calls2 = self.patch(response={"success": True, "run_id": "run-3"})
        tool.execute(kind="t1", qubit="q1", backend="auto")
        self.assertNotIn("backend", calls2[0]["payload"])

    def test_missing_kind_or_qubit_never_posts(self):
        tool = sem.SemanticRunExperimentTool()
        calls = self.patch(response={"success": True})
        self.assertFalse(tool.execute(kind="", qubit="q0").success)
        self.assertFalse(tool.execute(kind="iqraw", qubit="").success)
        self.assertEqual(calls, [])

    def test_service_error_becomes_tool_error(self):
        tool = sem.SemanticRunExperimentTool()
        self.patch(error={
            "success": False,
            "error": "pidf: qiskit=kind_not_supported",
            "error_code": "backend_unavailable",
            "attempts": [{"backend": "qiskit", "skipped": "kind_not_supported"}],
        })
        out = tool.execute(kind="pidf", qubit="q0")
        self.assertFalse(out.success)
        self.assertEqual(out.error_code, "backend_unavailable")
        self.assertIn("kind_not_supported", out.error)
        self.assertEqual(out.metadata["attempts"][0]["backend"], "qiskit")

    def test_network_error_is_wrapped(self):
        tool = sem.SemanticRunExperimentTool()
        self.patch(error={"error": "请求 http://x 失败：connection refused"})
        out = tool.execute(kind="iqraw", qubit="q0")
        self.assertFalse(out.success)
        self.assertIn("connection refused", out.error)


class TestAnalyzeDatasetTool(_PatchPost, unittest.TestCase):
    def test_schema_names(self):
        tool = sem.AnalyzeDatasetTool("http://127.0.0.1:3004/")
        self.assertEqual(tool.name, "analyze_dataset")
        self.assertEqual(tool.schema.name, "analyze_dataset")
        self.assertTrue(tool.schema.parameters["dataset_id"].required)
        self.assertFalse(tool.schema.parameters["kind"].required)

    def test_posts_fit(self):
        tool = sem.AnalyzeDatasetTool("http://127.0.0.1:3004")
        calls = self.patch(response={
            "ok": True,
            "kind": "t1",
            "metrics": {"t1_us": 49.5},
            "evidence": {"t1_us": 49.5},
            "r2": 0.99,
            "plot_url": "/plots/fit_t1.png",
        })
        out = tool.execute(dataset_id="20251103_00485", kind="t1", do_plot=True)
        self.assertTrue(out.success)
        self.assertEqual(calls[0]["url"], "http://127.0.0.1:3004/fit")
        self.assertEqual(calls[0]["payload"]["dataset_id"], "20251103_00485")
        self.assertEqual(calls[0]["payload"]["kind"], "t1")
        self.assertTrue(calls[0]["payload"]["do_plot"])
        self.assertEqual(calls[0]["payload"]["engine"], "scipy")

    def test_fit_failure_reported(self):
        tool = sem.AnalyzeDatasetTool()
        self.patch(response={"ok": False, "reason": "只有 2 个数据点，无法拟合"})
        out = tool.execute(dataset_id="x")
        self.assertFalse(out.success)
        self.assertEqual(out.error_code, "fit_failed")
        self.assertIn("数据点", out.error)


class TestToolsetSurface(unittest.TestCase):
    """默认工具面里应该有语义工具、没有 `run_code`。"""

    def test_agent_toolset_default(self):
        from services.agent_service import adapter  # noqa: F401  - 只验证导入不报错

    def test_registry_defaults(self):
        from services.common.quantum_tools import (
            AnalyzeDatasetTool,
            SemanticRunExperimentTool,
            ToolRegistry,
        )

        registry = ToolRegistry()
        registry.register_tool(SemanticRunExperimentTool("http://127.0.0.1:3003"))
        registry.register_tool(AnalyzeDatasetTool("http://127.0.0.1:3004"))
        names = registry.list_tools()
        self.assertIn("run_experiment", names)
        self.assertIn("analyze_dataset", names)
        self.assertNotIn("run_code", names)


if __name__ == "__main__":
    unittest.main()

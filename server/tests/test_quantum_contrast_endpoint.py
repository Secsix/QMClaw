"""/qubit/contrast 端点级测试（只读对比度分析）。

不打网络：直接 `object.__new__(QuantumService)` 调 handler，
把 `_submit_job` / `_ensure_connected` 换成桩，把上游口径函数也换成桩。

守住四件事：
1. 参数校验先于任何真机访问（缺 name / 非法 name / 非法 dataset_id / 非法 threshold
   一律 validation_error，不下发任何代码）；
2. 只读豁免写闸门：`allow_execute` 关闭时也不该被 read_only 拒掉；
3. 口径来自上游门面（handler 调的是 `build_contrast_code` / `annotate`，
   本仓不自己算一遍）；
4. 上游报 "没有匹配数据集 / 形状不对" 时回 validation_error 并带可诊断 hint。
"""
from __future__ import annotations

import pathlib
import sys
import threading
import unittest
from unittest import mock

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from services.quantum_service.server import QuantumService  # noqa: E402

try:  # 上游门面需要 submodule；没初始化就跳过（跟仓库其它"缺依赖记 SKIP"的规矩一致）
    from services.workflow_service.dsl.backends import lqcs as _upstream_lqcs
except Exception:  # noqa: BLE001
    _upstream_lqcs = None  # type: ignore[assignment]


class ContrastEndpointCase(unittest.TestCase):
    def setUp(self) -> None:
        if _upstream_lqcs is None:
            self.skipTest("上游 submodule 未初始化（vendor/OpenQWF），跳过")

        self.service = object.__new__(QuantumService)
        self.service._exec_stuck = None
        self.service._fallback = type("F", (), {"is_degraded": False})()
        self.service._ensure_connected = lambda: True
        self.service._jobs = {}
        self.service._job_events = {}
        self.service._current_task_id = None
        self.service._labrad = None

        # 假 job lane：立刻完成，stdout 里带上游的 QMCLAW_CONTRAST 行
        self.submitted: list[dict] = []

        def fake_submit_job(code, task_id, timeout, wait_event=None):
            self.submitted.append({"code": code, "task_id": task_id, "timeout": timeout})
            job_id = "job_test_1"
            stdout = (
                "[Quantum] exec: <下发代码回显>\n"
                "QMCLAW_CONTRAST 12 0.9 0.1 0.8 1.0 0.2 5.0 1000\n"
            )
            self.service._jobs[job_id] = {
                "job_id": job_id,
                "task_id": task_id,
                "status": "success",
                "result": {
                    "task_id": task_id,
                    "status": "success",
                    "stdout": stdout,
                    "stderr": "",
                    "error": "",
                    "result": {},
                },
            }
            if wait_event is not None:
                wait_event.set()
            return job_id

        self.service._submit_job = fake_submit_job  # type: ignore[assignment]

        self.build_patcher = mock.patch.object(
            _upstream_lqcs, "build_contrast_code", return_value="# stub code\n"
        )
        self.annotate_patcher = mock.patch.object(
            _upstream_lqcs,
            "annotate",
            side_effect=lambda kind, config, result: {
                **result,
                "dataset_id": 12,
                "metrics": {
                    "p1_i": 0.9,
                    "p1_x": 0.1,
                    "contrast": 0.8,
                    "separation": 1.0,
                    "sigma": 0.2,
                    "snr": 5.0,
                    "shots": 1000,
                },
                "contrast": 0.8,
                "passed": 0.8 >= float(config.get("threshold", 0.4)),
                "threshold": float(config.get("threshold", 0.4)),
            },
        )
        self.build_patcher.start()
        self.annotate_patcher.start()
        self.addCleanup(self.build_patcher.stop)
        self.addCleanup(self.annotate_patcher.stop)

    # ── 参数校验（先于真机访问） ─────────────────────────────────────────

    def test_missing_name_rejected(self) -> None:
        res = self.service._handle_qubit_contrast({})
        self.assertEqual(res.get("error_code"), "validation_error")
        self.assertEqual(self.submitted, [])  # 一次都没下发

    def test_unsafe_name_rejected(self) -> None:
        res = self.service._handle_qubit_contrast({"name": "q1; import os"})
        self.assertEqual(res.get("error_code"), "validation_error")
        self.assertEqual(self.submitted, [])

    def test_bad_dataset_id_rejected(self) -> None:
        res = self.service._handle_qubit_contrast({"name": "q1", "dataset_id": "abc"})
        self.assertEqual(res.get("error_code"), "validation_error")
        self.assertEqual(self.submitted, [])

    def test_bad_threshold_rejected(self) -> None:
        res = self.service._handle_qubit_contrast({"name": "q1", "threshold": "high"})
        self.assertEqual(res.get("error_code"), "validation_error")
        self.assertEqual(self.submitted, [])

    # ── 只读豁免写闸门 ───────────────────────────────────────────────────

    def test_read_only_bypasses_execute_gate(self) -> None:
        # 即使 allow_execute 关闭，只读对比度也不该被 read_only 拒掉
        with mock.patch.object(
            self.service, "_actuation_flags", return_value={"allow_execute": False}
        ):
            res = self.service._handle_qubit_contrast({"name": "q1"})
        self.assertTrue(res.get("success"), res)
        self.assertEqual(res.get("source"), "lqcs")
        self.assertEqual(len(self.submitted), 1)

    # ── 正常路径 ─────────────────────────────────────────────────────────

    def test_success_payload(self) -> None:
        res = self.service._handle_qubit_contrast({"name": "q1", "dataset_id": 12})
        self.assertTrue(res.get("success"), res)
        self.assertEqual(res.get("name"), "q1")
        self.assertEqual(res.get("dataset_id"), 12)
        self.assertEqual(res.get("contrast"), 0.8)
        self.assertEqual(res.get("metrics", {}).get("shots"), 1000)
        self.assertTrue(res.get("passed"))
        self.assertEqual(res.get("source"), "lqcs")
        self.assertIn("verdict", res)

    def test_uses_upstream_builder(self) -> None:
        with mock.patch.object(
            _upstream_lqcs, "build_contrast_code", return_value="# stub\n"
        ) as builder:
            self.service._handle_qubit_contrast({"name": "q1", "kind": "IQraw"})
        builder.assert_called_once()
        args = builder.call_args[0]
        self.assertEqual(args[1], "q1")
        self.assertEqual(args[2], "IQraw")

    # ── 上游报错 ─────────────────────────────────────────────────────────

    def test_upstream_error_becomes_validation_error(self) -> None:
        with mock.patch.object(
            _upstream_lqcs,
            "annotate",
            side_effect=lambda kind, config, result: {
                **result,
                "error": "no dataset found",
            },
        ):
            res = self.service._handle_qubit_contrast({"name": "q1"})
        self.assertEqual(res.get("error_code"), "validation_error")
        self.assertIn("hint", res)

    def test_not_connected_rejected(self) -> None:
        self.service._ensure_connected = lambda: False
        res = self.service._handle_qubit_contrast({"name": "q1"})
        self.assertEqual(res.get("error_code"), "not_connected")
        self.assertEqual(self.submitted, [])

    def test_degraded_rejected(self) -> None:
        self.service._fallback = type(
            "F", (), {"is_degraded": True, "is_connected": False}
        )()
        self.service._get_fallback_message = lambda: "LabRAD 不可用（降级模式）"
        res = self.service._handle_qubit_contrast({"name": "q1"})
        self.assertEqual(res.get("error_code"), "degraded")
        self.assertEqual(self.submitted, [])

    def test_stuck_rejected(self) -> None:
        self.service._exec_stuck = "Task t1 timed out"
        res = self.service._handle_qubit_contrast({"name": "q1"})
        self.assertEqual(res.get("error_code"), "stuck")
        self.assertEqual(self.submitted, [])


if __name__ == "__main__":
    unittest.main()

"""控制台演示剧本（`tools/demo_control_console.py`）的静态自检。

演示脚本本身要连服务（:3002/:3008），不方便在单测里跑；但脚本里有三块
**纯逻辑**必须守住，否则演示那天才发现 QWF 写错了：

1. 六份 QWF 模板能 parse + validate 干净（写错一个字段，演示当场失败）；
2. `metrics_to_evidence` 的换算口径（模拟后端给的是 `T1` 秒 / `separation`，
   状态机认的是 `t1_us` / `contrast`，换算错了状态就不动）；
3. `decide_stage_evidence` 的分支语义（run 有值用 run，没值退脚本口径）。

不联网、不起服务（纯静态 + 纯函数）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

DEMO_PATH = SERVER_ROOT / "tools" / "demo_control_console.py"
_spec = importlib.util.spec_from_file_location("demo_control_console", DEMO_PATH)
demo = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(demo)

from services.workflow_service.dsl import parse_qwf  # noqa: E402


def _validator():
    """兼容 `from .dsl import validate` 与 `from .dsl.validate import validate` 两种形态。

    （`services/workflow_service/dsl/validate.py` 是子模块，一旦有人 import 它，
    包属性 `validate` 就会被替换成 module —— 见 CLAUDE.md 的踩坑记录。）
    """
    import services.workflow_service.dsl as dsl

    candidate = getattr(dsl, "validate", None)
    if callable(candidate):
        return candidate
    from services.workflow_service.dsl.validate import validate

    return validate


class TestQwfTemplates(unittest.TestCase):
    def test_six_stages_defined(self) -> None:
        self.assertEqual(sorted(demo.QWF_TEMPLATES), [1, 2, 3, 4, 5, 6])
        self.assertEqual(sorted(demo.STAGE_TITLES), [1, 2, 3, 4, 5, 6])
        self.assertEqual(sorted(demo.STAGE_SCRIPT_EVIDENCE), [1, 2, 3, 4, 5, 6])

    def test_each_stage_parses_and_validates(self) -> None:
        validate = _validator()
        for stage in range(1, 7):
            with self.subTest(stage=stage):
                text = demo.stage_qwf(stage, "q0")
                self.assertNotIn("__QUBIT__", text)
                self.assertIn("q0", text)
                ir = parse_qwf(text)
                self.assertIsInstance(ir, dict)
                self.assertTrue(ir.get("id"))
                diags = validate(ir, require_writes=True)
                errors = [
                    d for d in diags
                    if getattr(d, "severity", None) == "error"
                    or (isinstance(d, dict) and d.get("severity") == "error")
                ]
                self.assertEqual(errors, [], f"阶段 {stage} 校验有 error：{errors}")

    def test_stage_qwf_rejects_unknown_stage(self) -> None:
        with self.assertRaises(KeyError):
            demo.stage_qwf(99, "q0")

    def test_qubit_substitution_is_isolated(self) -> None:
        # 模板本身不该被改动（演示中途换比特不会污染后续阶段）
        before = dict(demo.QWF_TEMPLATES)
        demo.stage_qwf(1, "q3")
        self.assertEqual(demo.QWF_TEMPLATES, before)


class TestMetricsToEvidence(unittest.TestCase):
    def test_contrast_direct(self) -> None:
        evidence, notes = demo.metrics_to_evidence({"contrast": 0.82})
        self.assertAlmostEqual(evidence["contrast"], 0.82)
        self.assertTrue(any("contrast" in n for n in notes))

    def test_separation_folded_to_contrast(self) -> None:
        evidence, _ = demo.metrics_to_evidence({"separation": 1.4})
        self.assertAlmostEqual(evidence["contrast"], 0.7)

    def test_separation_below_one_kept(self) -> None:
        evidence, _ = demo.metrics_to_evidence({"separation": 0.6})
        self.assertAlmostEqual(evidence["contrast"], 0.6)

    def test_snr_folded_to_contrast(self) -> None:
        evidence, _ = demo.metrics_to_evidence({"SNR": 10.0})
        self.assertAlmostEqual(evidence["contrast"], 0.5)

    def test_t1_seconds_to_microseconds(self) -> None:
        evidence, notes = demo.metrics_to_evidence({"T1": 4.5e-5})
        self.assertAlmostEqual(evidence["t1_us"], 45.0)
        self.assertTrue(any("t1_us" in n for n in notes))

    def test_t2_seconds_to_microseconds(self) -> None:
        evidence, _ = demo.metrics_to_evidence({"T2": 3.2e-5})
        self.assertAlmostEqual(evidence["t2_star_us"], 32.0)

    def test_gate_fidelity_mapped(self) -> None:
        evidence, _ = demo.metrics_to_evidence({"gate_fidelity": 0.995})
        self.assertAlmostEqual(evidence["xeb_gate_fidelity"], 0.995)

    def test_empty_metrics(self) -> None:
        evidence, notes = demo.metrics_to_evidence(None)
        self.assertEqual(evidence, {})
        self.assertEqual(notes, [])

    def test_bool_and_text_ignored(self) -> None:
        evidence, _ = demo.metrics_to_evidence(
            {"contrast": True, "T1": "n/a", "xeb_p": "0.99"}
        )
        self.assertNotIn("contrast", evidence)  # bool 不算数值
        self.assertNotIn("t1_us", evidence)
        # 字符串数字是允许的（后端有时把数值写成字符串）
        self.assertAlmostEqual(evidence["xeb_p"], 0.99)


class TestDecideStageEvidence(unittest.TestCase):
    def test_script_mode_returns_script_values(self) -> None:
        evidence, notes, reason = demo.decide_stage_evidence(
            3, {"contrast": 0.99}, mode="script"
        )
        self.assertEqual(evidence, {"contrast": 0.85})
        self.assertTrue(notes)
        self.assertIn("script", reason)

    def test_run_mode_uses_derived_value(self) -> None:
        evidence, _, reason = demo.decide_stage_evidence(
            3, {"contrast": 0.88}, mode="run"
        )
        self.assertAlmostEqual(evidence["contrast"], 0.88)
        self.assertIn("metrics", reason)

    def test_run_mode_falls_back_when_no_known_key(self) -> None:
        evidence, notes, reason = demo.decide_stage_evidence(
            4, {"foo": 1.0}, mode="run"
        )
        self.assertEqual(evidence, {"t1_us": 45.0})
        self.assertIn("脚本口径", reason)

    def test_run_failed_falls_back(self) -> None:
        evidence, _, reason = demo.decide_stage_evidence(
            4, {"T1": 4.5e-5}, mode="run", run_ok=False
        )
        self.assertEqual(evidence, {"t1_us": 45.0})
        self.assertIn("没有可用指标", reason)

    def test_stage1_derives_freq_locked(self) -> None:
        evidence, notes, _ = demo.decide_stage_evidence(
            1, {"contrast": 0.1}, mode="run"
        )
        self.assertTrue(evidence.get("freq_locked"))
        self.assertTrue(any("freq_locked" in n for n in notes))

    def test_stage_evidence_keys_are_known_to_state_machine(self) -> None:
        """写进去的键必须是状态机认的键，否则状态永远不会动。"""
        from services.common.qubit_state import EVIDENCE_SOURCES, STATES

        known = set(EVIDENCE_SOURCES)
        for stage, payload in demo.STAGE_SCRIPT_EVIDENCE.items():
            with self.subTest(stage=stage):
                for key in payload:
                    self.assertIn(key, known, f"阶段 {stage} 的证据键 {key} 状态机不认识")
        # 阶段号是演示脚本自己的编号（1..6），与状态机 state_index 不是一回事
        self.assertEqual(len(demo.STAGE_SCRIPT_EVIDENCE), len(STATES))


class TestArgParsing(unittest.TestCase):
    def test_unknown_stage_rejected_by_main(self) -> None:
        # main 不做网络请求前就会因为 stage 非法而返回非 0（不加 --base 也不该连网）
        rc = demo.main(["--stage", "9", "--base", "http://127.0.0.1:1", "--timeout", "0.1"])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

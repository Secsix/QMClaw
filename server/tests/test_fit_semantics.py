"""阶段2 收口：指标换算口径 + 语义拟合 + analysis_service 端点。

这些测试守的是「口径只有一份」这件事：

- ``services/common/metric_convert.py`` 是唯一换算实现（微服务 ``/metrics/convert``、
  演示脚本、循环脚本共用），所以这里盯死每个口径；
- ``services/analysis_service/fitting.py`` 的每个 kind 在合成数据上必须拟合出
  已知真值（模型正确性），失败时必须 ``ok=False`` 而不是拿初值冒充；
- ``/fit``、``/fit/kinds``、``/metrics/convert``、``/replay/plot`` 的入参校验与返回形状；
- workflow 的 analyze 节点不得再出现厂商调用（只允许注释里提到历史）。
"""

import math
import pathlib
import unittest

import numpy as np

from services.analysis_service import fitting
from services.common import metric_convert


# ── 指标换算 ──────────────────────────────────────────────────────────────


class TestMetricConvert(unittest.TestCase):
    def test_passthrough_evidence_keys(self):
        evidence, notes = metric_convert.to_evidence(
            {"t1_us": 55.0, "t2_star_us": 12.5, "xeb_p": 0.995, "xeb_gate_fidelity": 0.9975, "contrast": 0.82}
        )
        self.assertEqual(
            evidence,
            {"t1_us": 55.0, "t2_star_us": 12.5, "xeb_p": 0.995, "xeb_gate_fidelity": 0.9975, "contrast": 0.82},
        )
        self.assertTrue(all("直接取自" in note for note in notes))

    def test_seconds_to_microseconds(self):
        evidence, notes = metric_convert.to_evidence({"T1": 4.955e-05, "T2": 2.066e-05})
        self.assertAlmostEqual(evidence["t1_us"], 49.55, places=6)
        self.assertAlmostEqual(evidence["t2_star_us"], 20.66, places=6)
        self.assertTrue(any("秒 ×1e6" in note for note in notes))

    def test_us_keys_win_over_seconds(self):
        evidence, _ = metric_convert.to_evidence({"T1_us": 60.0, "T1": 1e-3})
        self.assertEqual(evidence["t1_us"], 60.0)

    def test_contrast_from_separation_and_snr(self):
        # separation > 1 表示「两峰间距超过 σ」，按 separation/2 折成 0..1
        self.assertAlmostEqual(metric_convert.to_evidence({"separation": 1.4142})[0]["contrast"], 0.7071, places=4)
        self.assertAlmostEqual(metric_convert.to_evidence({"separation": 0.8})[0]["contrast"], 0.8)
        self.assertAlmostEqual(metric_convert.to_evidence({"SNR": 4.0})[0]["contrast"], 0.2)

    def test_explicit_contrast_wins(self):
        evidence, _ = metric_convert.to_evidence({"contrast": 0.55, "separation": 1.9})
        self.assertEqual(evidence["contrast"], 0.55)

    def test_aliases(self):
        evidence, notes = metric_convert.to_evidence({"gate_fidelity": 0.995, "p_xeb": 0.994})
        self.assertEqual(evidence["xeb_gate_fidelity"], 0.995)
        self.assertEqual(evidence["xeb_p"], 0.994)
        self.assertTrue(any("gate_fidelity" in note for note in notes))

    def test_f10_zero_still_locks_frequency_with_note(self):
        evidence, notes = metric_convert.to_evidence({"f10": 0})
        self.assertTrue(evidence["freq_locked"])
        self.assertTrue(any("真机需 f10>0" in note for note in notes))

    def test_f10_positive_locks_frequency(self):
        evidence, _ = metric_convert.to_evidence({"f10": 4.9231e9})
        self.assertTrue(evidence["freq_locked"])

    def test_bool_is_not_a_number(self):
        evidence, _ = metric_convert.to_evidence({"contrast": True, "t1_us": False, "freq_locked": True})
        self.assertNotIn("contrast", evidence)
        self.assertNotIn("t1_us", evidence)
        self.assertTrue(evidence["freq_locked"])

    def test_string_numbers_parse_and_garbage_dropped(self):
        evidence, _ = metric_convert.to_evidence({"t1_us": "55.0", "t2_star_us": "not-a-number"})
        self.assertEqual(evidence["t1_us"], 55.0)
        self.assertNotIn("t2_star_us", evidence)

    def test_missing_keys_are_not_invented(self):
        evidence, notes = metric_convert.to_evidence({})
        self.assertEqual(evidence, {})
        self.assertEqual(notes, [])

    def test_input_not_mutated(self):
        metrics = {"T1": 5e-5, "separation": 1.2}
        snapshot = dict(metrics)
        metric_convert.to_evidence(metrics)
        self.assertEqual(metrics, snapshot)

    def test_describe_is_self_describing(self):
        schema = metric_convert.describe()
        self.assertIn("t1_us", schema["passthrough"])
        self.assertTrue(schema["unit_conversions"])
        self.assertIn("notes", schema)


# ── 拟合模型 ──────────────────────────────────────────────────────────────


class TestFitting(unittest.TestCase):
    def test_extract_series_shapes(self):
        x, y = fitting.extract_series(np.column_stack([np.arange(5.0), np.arange(5.0) * 2]))
        self.assertEqual(x.tolist(), [0, 1, 2, 3, 4])
        self.assertEqual(y.tolist(), [0, 2, 4, 6, 8])
        x2, y2 = fitting.extract_series({"x": [0, 1, 2], "y": [3, 4, 5]})
        self.assertEqual(x2.tolist(), [0, 1, 2])
        self.assertEqual(y2.tolist(), [3, 4, 5])
        x3, y3 = fitting.extract_series([1, 2, 3])
        self.assertEqual(y3.tolist(), [1, 2, 3])
        with self.assertRaises(ValueError):
            fitting.extract_series(None)

    def test_t1_recovers_tau(self):
        x = np.linspace(0, 200, 60)
        y = 0.9 * np.exp(-x / 55.0) + 0.1
        result = fitting.analyse("t1", np.column_stack([x, y]))
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["metrics"]["t1_us"], 55.0, delta=2.0)
        self.assertGreater(result["r2"], 0.95)

    def test_ramsey_recovers_t2_and_frequency(self):
        x = np.linspace(0, 200, 120)
        y = 0.8 * np.exp(-x / 120.0) * np.cos(2 * np.pi * 0.02 * x) + 0.5
        result = fitting.analyse("ramsey_df", np.column_stack([x, y]))
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["metrics"]["t2_star_us"], 120.0, delta=5.0)
        self.assertAlmostEqual(result["metrics"]["ramsey_freq"], 0.02, delta=0.002)

    def test_ramsey_aliases_share_one_model(self):
        for name in ("ramsey", "ramsey_df", "ramsey_t2"):
            self.assertIn(name, fitting.FITTERS, name)
            self.assertIs(fitting.FITTERS[name], fitting.FITTERS["ramsey"], name)

    def test_xeb_decay_and_fidelity_conversion(self):
        x = np.linspace(0, 60, 40)
        y = 0.85 * np.power(0.995, x) + 0.5
        result = fitting.analyse("xeb", np.column_stack([x, y]))
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["metrics"]["xeb_p"], 0.995, delta=0.002)
        p = result["metrics"]["xeb_p"]
        self.assertAlmostEqual(result["metrics"]["xeb_gate_fidelity"], 1 - (1 - p) / 2, places=5)

    def test_spectroscopy_finds_dip_center(self):
        x = np.linspace(4.0, 5.0, 200)
        y = -0.8 * (0.005 ** 2) / ((x - 4.5) ** 2 + 0.005 ** 2) + 1.0
        result = fitting.analyse("spectroscopy", np.column_stack([x, y]))
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["metrics"]["f10"], 4.5, delta=0.01)

    def test_iqraw_contrast_from_two_clusters(self):
        rng = np.random.default_rng(0)
        low = rng.normal(0.0, 0.05, 400)
        high = rng.normal(1.0, 0.05, 400)
        values = np.concatenate([low, high])
        result = fitting.analyse("iqraw", np.column_stack([np.arange(values.size), values]))
        self.assertTrue(result["ok"])
        self.assertGreater(result["metrics"]["separation"], 10.0)
        self.assertLessEqual(result["metrics"]["contrast"], 1.0)

    def test_drag_parabola_vertex(self):
        x = np.linspace(-0.05, 0.05, 41)
        y = 2.0 * (x - 0.012) ** 2 + 0.3
        result = fitting.analyse("drag_calibrate", np.column_stack([x, y]))
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["metrics"]["drag_delta"], 0.012, delta=0.002)

    def test_piamp_sinusoid_period(self):
        x = np.linspace(0, 1, 200)
        y = np.sin(2 * np.pi * 2.0 * x) + 0.5
        result = fitting.analyse("time_rabi", np.column_stack([x, y]))
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["params"]["period"], 0.5, delta=0.05)

    def test_unknown_kind_reports_available(self):
        result = fitting.analyse("not_a_kind", {"x": [0, 1, 2, 3], "y": [1, 1, 1, 1]})
        self.assertFalse(result["ok"])
        self.assertIn("不支持的 kind", result["reason"])
        self.assertTrue(result["kinds"])

    def test_too_few_points(self):
        result = fitting.analyse("t1", {"x": [0, 1], "y": [1, 0.5]})
        self.assertFalse(result["ok"])
        self.assertIn("数据点太少", result["reason"])

    def test_failure_never_fakes_metrics(self):
        # 完全噪声的数据：拟合要么不收敛、要么 r² 低，但都不得凭空给出 metrics 之外的东西
        rng = np.random.default_rng(7)
        x = np.linspace(0, 1, 30)
        y = rng.normal(0, 1, 30)
        result = fitting.analyse("t1", np.column_stack([x, y]))
        if result["ok"]:
            self.assertIn("t1_us", result["metrics"])
            self.assertTrue(result.get("warnings"))
        else:
            self.assertNotIn("metrics", result)

    def test_lqcs_engine_reports_reason(self):
        x = np.linspace(0, 100, 30)
        y = np.exp(-x / 30.0)
        result = fitting.analyse("t1", {"x": x.tolist(), "y": y.tolist()}, engine="lqcs")
        # 没装 LQCS 时必须明确失败，并说明原因（不允许静默退化成 scipy 还声称是 lqcs）
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("reason"))


# ── analysis_service 端点 ─────────────────────────────────────────────────


def _service():
    from services.analysis_service.server import AnalysisService

    svc = object.__new__(AnalysisService)
    svc._default_dpi = 80
    svc._offline_provider = None
    return svc


class TestAnalysisEndpoints(unittest.TestCase):
    def test_fit_kinds(self):
        payload = _service().handle_request("GET", "/fit/kinds", {}, {})
        self.assertTrue(payload["success"])
        self.assertIn("t1", payload["kinds"])
        self.assertIn("ramsey_df", payload["kinds"])
        self.assertIn("scipy", payload["engines"])

    def test_metrics_convert_endpoint(self):
        payload = _service().handle_request(
            "POST", "/metrics/convert", {"metrics": {"T1": 5e-5, "separation": 1.2}}, {}
        )
        self.assertTrue(payload["success"])
        self.assertAlmostEqual(payload["evidence"]["t1_us"], 50.0, places=6)
        self.assertAlmostEqual(payload["evidence"]["contrast"], 0.6, places=6)
        self.assertTrue(payload["notes"])

    def test_metrics_convert_rejects_non_object(self):
        payload = _service().handle_request("POST", "/metrics/convert", {"metrics": [1, 2]}, {})
        self.assertFalse(payload["success"])

    def test_metrics_schema(self):
        payload = _service().handle_request("GET", "/metrics/schema", {}, {})
        self.assertTrue(payload["success"])
        self.assertIn("unit_conversions", payload)

    def test_fit_inline_series(self):
        x = np.linspace(0, 200, 40).tolist()
        y = (0.9 * np.exp(-np.linspace(0, 200, 40) / 55.0) + 0.1).tolist()
        payload = _service().handle_request("POST", "/fit", {"x": x, "y": y, "kind": "t1"}, {})
        self.assertTrue(payload["success"])
        self.assertAlmostEqual(payload["metrics"]["t1_us"], 55.0, delta=2.0)
        self.assertIn("t1_us", payload["evidence"])

    def test_fit_requires_dataset_or_series(self):
        payload = _service().handle_request("POST", "/fit", {"kind": "t1"}, {})
        self.assertFalse(payload["success"])

    def test_fit_unknown_kind(self):
        payload = _service().handle_request(
            "POST", "/fit", {"x": [0, 1, 2, 3], "y": [1, 1, 1, 1], "kind": "nope"}, {}
        )
        self.assertFalse(payload["success"])

    def test_fit_bad_engine(self):
        payload = _service().handle_request(
            "POST", "/fit", {"x": [0, 1, 2, 3], "y": [1, 0.9, 0.8, 0.7], "kind": "t1", "engine": "magic"}, {}
        )
        self.assertFalse(payload["success"])

    def test_fit_do_plot_writes_file(self):
        x = np.linspace(0, 200, 40)
        payload = _service().handle_request(
            "POST",
            "/fit",
            {"x": x.tolist(), "y": (np.exp(-x / 55.0)).tolist(), "kind": "t1", "do_plot": True},
            {},
        )
        self.assertTrue(payload["success"])
        url = payload.get("plot_url", "")
        self.assertTrue(url.startswith("/plots/"), url)
        path = pathlib.Path(__file__).parent.parent.parent / "web" / "public" / url.lstrip("/")
        self.assertTrue(path.exists(), path)

    def test_replay_plot_defaults_to_fit_with_plot(self):
        x = np.linspace(0, 120, 30)
        payload = _service().handle_request(
            "POST", "/replay/plot", {"x": x.tolist(), "y": (np.exp(-x / 40.0)).tolist(), "kind": "t1"}, {}
        )
        self.assertTrue(payload["success"])
        self.assertTrue(payload.get("plot_url"))


# ── workflow analyze 节点不再碰厂商调用 ──────────────────────────────────


class TestWorkflowAnalyzeBoundary(unittest.TestCase):
    def test_no_vendor_fit_command_in_code(self):
        path = pathlib.Path(__file__).parent.parent / "services" / "workflow_service" / "server.py"
        text = path.read_text(encoding="utf-8")
        offenders = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            allowed = stripped.startswith("#") or "must start with" in stripped
            if "qter.fitData" in stripped and not allowed:
                offenders.append((lineno, stripped))
        self.assertEqual(offenders, [], f"workflow 仍在拼厂商调用：{offenders}")


if __name__ == "__main__":
    unittest.main()

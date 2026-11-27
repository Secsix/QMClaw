# -*- coding: utf-8 -*-
"""标定口径测试：指标计算 + 门限来自 lab_criteria.json。

原用例来自已出库的 tools/single_qubit_auto_calibration/tests/test_metrics.py，
迁移到服务端后改为断言 calibration_metrics 的公开函数。
"""
import os
import sys
import unittest

import numpy as np

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SERVER_ROOT not in sys.path:
    sys.path.insert(0, SERVER_ROOT)

from services.analysis_service.calibration_metrics import (  # noqa: E402
    choose_strategy,
    final_pass,
    gate_thresholds,
    iq_fast_pass,
    iq_final_pass,
    iq_metrics,
    rabi_metrics,
    rabi_pass,
    ramsey_metrics,
    s21_metrics,
    spectroscopy_metrics,
)


def _iq_cols(seed=2, shift=8.0):
    rng = np.random.default_rng(seed)
    n = 500
    cols = {
        "Is | I": rng.normal(0.0, 1.0, n),
        "Qs | I": rng.normal(0.0, 1.0, n),
        "Is | X": rng.normal(shift, 1.0, n),
        "Qs | X": rng.normal(shift, 1.0, n),
    }
    return cols


class TestIqMetrics(unittest.TestCase):
    def test_separable_clusters(self):
        m = iq_metrics(_iq_cols())
        self.assertTrue(m["valid"])
        self.assertGreater(m["snr"], 5.0)
        self.assertGreater(m["norm"], 0.5)
        self.assertGreaterEqual(m["fidelity"], 0.9)

    def test_overlapping_clusters_fail(self):
        cols = {
            "Is | I": np.zeros(50),
            "Qs | I": np.zeros(50),
            "Is | X": np.zeros(50),
            "Qs | X": np.zeros(50),
        }
        m = iq_metrics(cols)
        self.assertTrue(m["valid"])
        self.assertFalse(iq_fast_pass(m))

    def test_missing_columns(self):
        m = iq_metrics({"Is | I": np.zeros(4)})
        self.assertFalse(m["valid"])
        self.assertEqual(m["reason"], "missing_iq_columns")


class TestRabiMetrics(unittest.TestCase):
    def _cols(self):
        amp = np.arange(0, 3.01, 0.2)
        p1 = 0.5 - 0.45 * np.cos(2 * np.pi * amp / 2)
        return {"piAmp": amp, "P1": p1}

    def test_oscillation_detected(self):
        m = rabi_metrics(self._cols())
        self.assertTrue(m["ok"])
        self.assertAlmostEqual(m["amp_opt"], 1.0, delta=0.3)

    def test_flat_curve_not_ok(self):
        cols = self._cols()
        cols["P1"] = np.full_like(cols["P1"], 0.5)
        m = rabi_metrics(cols)
        self.assertFalse(m["ok"])

    def test_monotonic_curve_not_ok(self):
        cols = self._cols()
        cols["P1"] = np.linspace(0.1, 0.5, len(cols["piAmp"]))
        m = rabi_metrics(cols)
        self.assertFalse(m["ok"])


class TestOtherMetrics(unittest.TestCase):
    def test_spectroscopy_peak(self):
        freq = np.linspace(5.0, 6.0, 51)
        p1 = np.exp(-((freq - 5.42) ** 2) / 0.002)
        m = spectroscopy_metrics({"freq": freq, "P1": p1})
        self.assertTrue(m["valid"])
        self.assertAlmostEqual(m["f10"], 5.42, delta=0.05)

    def test_s21_metrics(self):
        freq = np.linspace(5.0, 6.0, 51)
        amp0 = (freq - 5.3) ** 2
        amp1 = (freq - 5.7) ** 2
        dist = -np.abs(freq - 5.5)
        m = s21_metrics(
            {"freq": freq, "Amplitude | 0": amp0, "Amplitude | 1": amp1, "distance | 01": dist}
        )
        self.assertAlmostEqual(m["fread_amp0"], 5.3, delta=0.05)
        self.assertAlmostEqual(m["fread_amp1"], 5.7, delta=0.05)
        self.assertAlmostEqual(m["fread_best"], 5.5, delta=0.05)

    def test_ramsey_requires_external_fit(self):
        m = ramsey_metrics({"delay": np.linspace(0, 1, 10), "P1": np.linspace(1, 0, 10)})
        self.assertFalse(m["valid"])
        self.assertEqual(m["reason"], "ramsey_fit_requires_external_fit")

    def test_ramsey_with_detune(self):
        m = ramsey_metrics({"detune_MHz": np.array([0.02]), "T2star_us": np.array([12.0])})
        self.assertTrue(m["valid"])
        self.assertAlmostEqual(m["detune_mhz"], 0.02)
        self.assertAlmostEqual(m["T2star_us"], 12.0)


class TestThresholdsAndStrategy(unittest.TestCase):
    def test_thresholds_come_from_criteria(self):
        th = gate_thresholds()
        self.assertEqual(th["rabi_swing_min"], 0.15)
        self.assertEqual(th["min_readout_fidelity"], 0.8)
        self.assertEqual(th["min_t1_us"], 20.0)

    def test_choose_strategy_reoptimize_f10_and_piamp(self):
        m = iq_metrics(_iq_cols())
        s = choose_strategy(s21_ok=True, zpa_ok=True, iq=m, rabi={"ok": False})
        self.assertEqual(s, "REOPTIMIZE_F10_AND_PIAMP")

    def test_choose_strategy_needs_s21_first(self):
        m = iq_metrics(_iq_cols())
        s = choose_strategy(s21_ok=False, zpa_ok=True, iq=m, rabi={"ok": False})
        self.assertEqual(s, "RETRY_S21_OR_TRY_SECOND_CAVITY")

    def test_final_pass_gates(self):
        th = gate_thresholds()
        iq = {"valid": True, "snr": 5.0, "norm": 0.6, "fidelity": 0.95}
        rabi = {"ok": True, "swing": 0.4, "score": 6.0}
        self.assertTrue(iq_final_pass(iq, th))
        self.assertTrue(rabi_pass(rabi, th))
        self.assertTrue(final_pass(iq, rabi, {"detune_mhz": 0.01}, 30.0, th))
        self.assertFalse(final_pass(iq, rabi, {"detune_mhz": 0.5}, 30.0, th))
        self.assertFalse(final_pass(iq, rabi, {"detune_mhz": 0.01}, 5.0, th))


if __name__ == "__main__":
    unittest.main()

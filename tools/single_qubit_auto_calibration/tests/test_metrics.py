import unittest
import numpy as np

from tools.single_qubit_auto_calibration.analysis.metrics import iq_metrics, rabi_metrics
from tools.single_qubit_auto_calibration.analysis.quality_gate import choose_strategy
from tools.single_qubit_auto_calibration.config import load_config


class TestMetrics(unittest.TestCase):
    def test_iq_and_strategy(self):
        rng = np.random.default_rng(2)
        cols = {"Is | I": rng.normal(0, 1, 500), "Qs | I": rng.normal(0, 1, 500),
                "Is | X": rng.normal(8, 1, 500), "Qs | X": rng.normal(8, 1, 500)}
        m = iq_metrics(cols)
        self.assertTrue(m["valid"])
        self.assertGreater(m["snr"], 5)
        self.assertEqual(choose_strategy(s21_ok=True, zpa_ok=True, iq=m, rabi={"ok": False}, cfg=load_config()), "REOPTIMIZE_F10_AND_PIAMP")

    def test_rabi(self):
        amp = np.arange(0, 3.01, 0.2)
        m = rabi_metrics({"piAmp": amp, "P1": 0.5 - 0.45 * np.cos(2 * np.pi * amp / 2.0)})
        self.assertTrue(m["ok"])
        self.assertAlmostEqual(m["amp_opt"], 1.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()

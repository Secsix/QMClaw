import unittest
import numpy as np

from tools.single_qubit_auto_calibration.adapters.mock import MockAdapter
from tools.single_qubit_auto_calibration.config import load_config
from tools.single_qubit_auto_calibration.models import CalibrationState, QubitInput
from tools.single_qubit_auto_calibration.reporting.report import write_report
from tools.single_qubit_auto_calibration.workflow import CalibrationWorkflow


def good_iq(*args, **kwargs):
    rng = np.random.default_rng(4)
    return {"columns": {"Is | I": rng.normal(0, 1, 400), "Qs | I": rng.normal(0, 1, 400),
                         "Is | X": rng.normal(8, 1, 400), "Qs | X": rng.normal(8, 1, 400)}}


def good_pi(*args, **kwargs):
    amp = np.arange(0, 3.01, 0.2)
    return {"columns": {"piAmp": amp, "P1": 0.5 - 0.45 * np.cos(2 * np.pi * amp / 2.0)}}


class TestWorkflow(unittest.TestCase):
    def test_mock_full_run_and_plan(self):
        adapter = MockAdapter({
            "s21": {"columns": {"freq": [6.39, 6.40, 6.41], "Amplitude | 0": [1, 0, 1], "Amplitude | 1": [1, 0.1, 1]}},
            "spectroscopy": {"columns": {"freq": [4.1, 4.2, 4.3], "P1": [0.1, 0.8, 0.1]}},
            "iqraw": good_iq,
            "piamp": good_pi,
            "s21_dis": {"columns": {"freq": [6.39, 6.40, 6.41], "Amplitude | 0": [1, 0, 1], "distance | 01": [0.1, 0.9, 0.2]}},
            "ramsey_df": {"columns": {"detune_MHz": [0.02], "T2star_us": [40]}},
            "t1": {"T1_us": 30.0},
        })
        run = CalibrationWorkflow(adapter, load_config()).run(QubitInput("q1", 6.4, 0.0, 0.0))
        self.assertEqual(run.state, CalibrationState.COMPLETE)
        self.assertIsNotNone(run.final)
        out = "D:/QMClaw/tools/single_qubit_auto_calibration/tests/_out"
        plan = write_report(run, out)
        self.assertTrue(plan["requires_approval"])
        self.assertTrue(plan["dry_run"])


if __name__ == "__main__":
    unittest.main()

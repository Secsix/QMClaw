"""qubit_health_report 自检：验证解码与指标函数的行为。

    python test_health_report.py
"""
from __future__ import annotations

import base64
import struct
import sys
import unittest

import numpy as np

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from dvread import decode_labrad_value, parse_name  # noqa: E402
from metrics import iqraw_metrics, rabi_fit, ramsey_fit  # noqa: E402


def _float_param(v: float) -> str:
    """构造 LabRAD tagged data（float64 大端）的 base64 data URL。"""
    payload = struct.pack(">d", v)
    raw = b"\x00\x00\x00\x00" + b"v" + payload[:6] + payload
    return "data:application/labrad;base64," + base64.urlsafe_b64encode(raw).decode()


class TestDecode(unittest.TestCase):
    def test_float_roundtrip(self):
        for v in (0.0, 1.5, -36.0, 6.318337221133414, 4.118426365418829):
            raw = base64.urlsafe_b64decode(_float_param(v).split("base64,")[1])
            self.assertAlmostEqual(decode_labrad_value(raw), v, places=12)

    def test_parse_name(self):
        from pathlib import Path
        idx, qubit, exp = parse_name(Path("03649 - q21_5%c IQraw.hdf5"))
        self.assertEqual((idx, qubit, exp), (3649, "q21_5", "IQraw"))


class TestIQLraw(unittest.TestCase):
    def test_separable(self):
        n = 500
        rng = np.random.default_rng(0)
        i0 = rng.normal(0, 1, n)
        q0 = rng.normal(0, 1, n)
        i1 = rng.normal(8, 1, n)
        q1 = rng.normal(8, 1, n)
        m = iqraw_metrics({"Is | I": i0, "Qs | I": q0, "Is | X": i1, "Qs | X": q1})
        self.assertGreater(m["snr"], 5)
        self.assertGreater(m["norm"], 0.5)
        self.assertGreater(m["fidelity"], 0.99)

    def test_identical(self):
        n = 300
        rng = np.random.default_rng(1)
        i = rng.normal(0, 1, n)
        q = rng.normal(0, 1, n)
        m = iqraw_metrics({"Is | I": i, "Qs | I": q, "Is | X": i, "Qs | X": q})
        self.assertLess(m["snr"], 0.1)
        self.assertLess(m["norm"], 0.1)


class TestRabi(unittest.TestCase):
    def test_real_oscillation(self):
        amp = np.arange(0, 3.01, 0.2)
        p1 = 0.5 - 0.45 * np.cos(2 * np.pi * amp / 2.0)   # 周期 2.0，峰在 amp=1.0
        m = rabi_fit({"piAmp": amp, "P1": p1})
        self.assertTrue(m["ok"])
        self.assertGreater(m["swing"], 0.3)
        self.assertAlmostEqual(m["amp_opt"], 1.0, delta=0.3)

    def test_flat(self):
        amp = np.arange(0, 3.01, 0.2)
        p1 = np.full_like(amp, 0.4)
        m = rabi_fit({"piAmp": amp, "P1": p1})
        self.assertFalse(m["ok"])

    def test_monotonic_rise(self):
        """单调上升（未到峰）不算 Rabi 振荡。"""
        amp = np.arange(0, 3.01, 0.2)
        p1 = 0.05 + 0.1 * amp
        m = rabi_fit({"piAmp": amp, "P1": p1})
        self.assertFalse(m["ok"])


class TestRamsey(unittest.TestCase):
    def test_known_t2(self):
        delay = np.arange(0, 1001, 20.0)
        p1 = 0.5 - 0.4 * np.exp(-delay / 300.0) * np.cos(2 * np.pi * delay / 200.0)
        m = ramsey_fit({"delay": delay, "P1": p1})
        self.assertTrue(m["ok"])
        self.assertAlmostEqual(m["T2"], 300, delta=60)
        self.assertAlmostEqual(m["period_ns"], 200, delta=20)

    def test_noise(self):
        rng = np.random.default_rng(3)
        delay = np.arange(0, 1001, 20.0)
        p1 = 0.4 + rng.normal(0, 0.03, delay.size)
        m = ramsey_fit({"delay": delay, "P1": p1})
        self.assertFalse(m.get("valid", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)

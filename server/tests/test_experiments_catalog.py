"""实验语义层：目录 / 代码拼装 / 三后端适配 / 端点校验（2026-11-26 收口）。

这层的验收口径只有一句话：**调用方说"跑 t1"，服务自己知道该拼什么代码、
该用哪个后端**。所以测试覆盖：

1. ``catalog``：kind 的唯一真源，15 个已登记实验都在，lqcs 专属 kind 的
   qiskit 支持位必须是 False（否则 Router 会把它当成能跑的候选）；
2. ``codegen``：语义参数 → vendor 调用代码（含 range→np.arange、多比特、
   耦合器），并拒绝非法比特名 / 参数名；
3. ``adapters``：能力判定、显式后端不被策略偷换、失败留 attempts 说明；
4. 端点层：``/experiments/run`` 的入参校验（kind / qubit / params）。
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List

from services.quantum_service.experiments import adapters, catalog
from services.quantum_service.experiments.base import (
    BackendError,
    ExperimentRequest,
    ExperimentResult,
)
from services.quantum_service.experiments.codegen import (
    build_vendor_call,
    normalize_params,
)

REGISTERED = (
    "spectroscopy", "s21", "iqraw", "t1", "ramsey", "piamp", "xeb", "s21_dis",
    "allxy", "single_shot", "pulsed_spec", "swap", "drag_calibrate", "cz.dcz",
)


class TestCatalog(unittest.TestCase):
    def setUp(self):
        catalog.reload()

    def test_registered_kinds_all_present(self):
        for kind in REGISTERED:
            self.assertIsNotNone(catalog.get_spec(kind), f"{kind} 应该在目录里")

    def test_every_kind_has_at_least_one_backend(self):
        for kind, spec in catalog.CATALOG.items():
            self.assertTrue(
                any(spec["backends"].values()),
                f"{kind} 三个后端都不支持，Router 会拿到一个永远跑不动的候选",
            )

    def test_lqcs_only_kind_is_not_claimed_by_qiskit(self):
        spec = catalog.get_spec("pidf")
        self.assertTrue(spec["backends"]["lqcs"])
        self.assertFalse(spec["backends"]["qiskit"], "pidf 只在真机上有，不能骗 Router 说 qiskit 能跑")

    def test_qiskit_alias_mapping(self):
        # ramsey_df 在真机叫 ramsey_df，qiskit 词表里叫 ramsey
        self.assertTrue(catalog.get_spec("ramsey_df")["backends"]["qiskit"])
        self.assertTrue(catalog.get_spec("pulsed_spec")["backends"]["qiskit"])

    def test_cz_requires_coupler_param(self):
        params = catalog.get_spec("cz.dcz")["params"]
        self.assertIn("coupler", params)
        self.assertTrue(params["coupler"].get("required"))

    def test_catalog_json_contract(self):
        payload = catalog.catalog_json()
        self.assertEqual(payload["kinds"], len(payload["experiments"]))
        self.assertEqual(set(payload["backends"]), {"lqcs", "qiskit", "offline_sim"})
        for item in payload["experiments"]:
            self.assertIn("fn", item)
            self.assertIn("backends", item)
            self.assertIn("params", item)
            self.assertEqual(
                item["supported_backends"],
                [b for b in ("lqcs", "qiskit", "offline_sim") if item["backends"][b]],
            )

    def test_unknown_kind_is_none(self):
        self.assertIsNone(catalog.get_spec("no_such_experiment"))
        self.assertFalse(catalog.supports("no_such_experiment", "qiskit"))

    def test_supported_kinds_filter(self):
        self.assertIn("pidf", catalog.supported_kinds("lqcs"))
        self.assertNotIn("pidf", catalog.supported_kinds("qiskit"))
        self.assertIn("t1", catalog.supported_kinds("qiskit"))


class TestCodegen(unittest.TestCase):
    def test_simple_single_qubit(self):
        code = build_vendor_call("sq.iqraw", ["q0"], {"shots": 2000}, report_dataset=False)
        self.assertTrue(code.startswith("sq.iqraw("))
        self.assertIn("shots=2000", code)

    def test_range_params_become_arange(self):
        code = build_vendor_call(
            "sq.spectroscopy", ["q0"], {"freq_range": [4.0, 3.0, -0.01]}, report_dataset=False
        )
        self.assertIn("freq=", code)
        self.assertIn("arange(4.0, 3.0, -0.01)", code)

    def test_multi_qubit_positional(self):
        code = build_vendor_call("sq.swap", ["q0", "q1"], {}, report_dataset=False)
        self.assertIn("sq.swap(", code)
        self.assertIn("q0", code)
        self.assertIn("q1", code)

    def test_coupler_for_class_call(self):
        code = build_vendor_call("cz.dcz", ["q0", "q1"], {}, coupler="c2", report_dataset=False)
        self.assertIn("DCZTuner", code)
        self.assertIn("c2", code)

    def test_rejects_bad_param_name(self):
        with self.assertRaises(ValueError):
            build_vendor_call("sq.iqraw", ["q0"], {"bad-name": 1}, report_dataset=False)

    def test_rejects_bad_qubit_name(self):
        with self.assertRaises(ValueError):
            build_vendor_call("sq.iqraw", ["q0; rm -rf /"], {}, report_dataset=False)

    def test_rejects_empty_qubits(self):
        with self.assertRaises(ValueError):
            build_vendor_call("sq.iqraw", [], {}, report_dataset=False)

    def test_normalize_params_alias_and_arange(self):
        out = normalize_params({"freq_range": [1, 2, 3], "do_plot": False, "zpa": 0.0})
        self.assertEqual(out["freq"], {"$arange": [1, 2, 3]})
        self.assertIs(out["do_plot"], False)
        self.assertNotIn("freq_range", out)


class _FakePolicy:
    def __init__(self, order, active="qiskit"):
        self._order = list(order)
        self._active = active

    def resolve(self, mode=None):
        return list(self._order)

    def active(self, mode=None):
        return self._active

    class _State:
        available = True

    def state(self, name):
        return self._State()


class _FakeService:
    """只实现适配器用到的那几个钩子，不碰硬件。"""

    def __init__(self, order=("lqcs", "qiskit", "offline_sim"), active="qiskit", payload=None):
        self._policy = _FakePolicy(order, active)
        self.payload = payload or {"status": "qiskit_completed", "backend": active, "metrics": {"T1": 1.0}}
        self.calls: List[Dict[str, Any]] = []

    def _get_mode(self):
        return "auto"

    def _handle_execute(self, body):
        self.calls.append(dict(body))
        payload = dict(self.payload)
        if body.get("_force_backend"):
            payload["backend"] = body["_force_backend"]
        return payload


class TestAdapters(unittest.TestCase):
    def setUp(self):
        catalog.reload()

    def test_supports_uses_catalog(self):
        svc = _FakeService()
        self.assertTrue(adapters.adapter_for(svc, "qiskit").supports("t1"))
        self.assertFalse(adapters.adapter_for(svc, "qiskit").supports("pidf"))

    def test_forced_backend_reaches_execute(self):
        svc = _FakeService()
        request = ExperimentRequest(kind="t1", qubits=("q0",), params={"zpa": 0.0})
        outcome = adapters.run_experiment(svc, request, "qiskit")
        self.assertEqual(outcome["result"].backend, "qiskit")
        self.assertEqual(svc.calls[0]["_force_backend"], "qiskit")
        self.assertIn("zpa=0.0", svc.calls[0]["code"])

    def test_offline_forced_backend(self):
        svc = _FakeService()
        request = ExperimentRequest(kind="iqraw", qubits=("q0",))
        adapters.run_experiment(svc, request, "offline_sim")
        self.assertEqual(svc.calls[0]["_force_backend"], "offline_sim")

    def test_unknown_kind_raises(self):
        svc = _FakeService()
        with self.assertRaises(BackendError):
            adapters.run_experiment(svc, ExperimentRequest(kind="nope", qubits=("q0",)))

    def test_unsupported_backend_records_attempt(self):
        svc = _FakeService()
        with self.assertRaises(BackendError) as ctx:
            adapters.run_experiment(svc, ExperimentRequest(kind="pidf", qubits=("q0",)), "qiskit")
        self.assertIn("pidf", str(ctx.exception))

    def test_auto_skips_unavailable_and_unsupported(self):
        # pidf：lqcs 支持但当前生效后端不是 lqcs（跳过），qiskit 不支持（跳过），
        # offline_sim 收下 → 说明"按策略逐个试、每个失败都留痕"这条链是通的
        svc = _FakeService(order=("lqcs", "qiskit", "offline_sim"), active="qiskit")
        outcome = adapters.run_experiment(svc, ExperimentRequest(kind="pidf", qubits=("q0",)))
        self.assertTrue(outcome["result"].ok)
        self.assertEqual(outcome["result"].backend, "offline_sim")
        skipped = {a["backend"]: a.get("skipped") for a in outcome["attempts"]}
        self.assertEqual(skipped.get("qiskit"), "kind_not_supported")
        self.assertEqual(skipped.get("lqcs"), "backend_error")

    def test_backend_status_shape(self):
        svc = _FakeService()
        rows = {row["backend"]: row for row in adapters.backend_status(svc)}
        self.assertEqual(set(rows), {"lqcs", "qiskit", "offline_sim"})
        for row in rows.values():
            self.assertIn("available", row)
            self.assertIn("kinds", row)


class TestRunEndpointValidation(unittest.TestCase):
    def _service(self):
        from services.quantum_service.server import QuantumService

        return object.__new__(QuantumService)

    def test_requires_kind(self):
        payload = self._service().handle_request("POST", "/experiments/run", {"qubit": "q0"}, {})
        self.assertFalse(payload["success"])
        self.assertIn("kind", payload["error"])

    def test_requires_qubit(self):
        payload = self._service().handle_request("POST", "/experiments/run", {"kind": "t1"}, {})
        self.assertFalse(payload["success"])
        self.assertIn("qubit", payload["error"])

    def test_rejects_bad_params_type(self):
        payload = self._service().handle_request(
            "POST", "/experiments/run", {"kind": "t1", "qubit": "q0", "params": [1, 2]}, {}
        )
        self.assertFalse(payload["success"])
        self.assertIn("params", payload["error"])


if __name__ == "__main__":
    unittest.main()

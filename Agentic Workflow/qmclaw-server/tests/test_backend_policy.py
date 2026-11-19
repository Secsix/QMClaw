"""tests/test_backend_policy.py —— 实验后端选择策略的单元测试

覆盖三件事：

1. **顺序解析**：``mode``(online/offline/auto) 与配置 ``order`` 的映射，
   以及运行期 ``prefer`` 只影响 auto、不影响硬指定的 online/offline。
2. **探测健壮性**：探测函数抛异常不会冒泡；结果按 TTL 缓存；``peek_*``
   绝不触发探测（高频 /health 用）。
3. **服务层行为**：``/execute`` 在 qiskit 生效时走 Aer；``/qubits`` 在
   "本机没有真机栈"时给演示比特，而在 online 模式下**不给**（不能拿假比特
   糊弄现场人员）。

不依赖 qiskit 是否安装：探测函数全部是注入的假实现。
"""

import unittest

from services.quantum_service import backend_policy as bp


def _state(name, available, reason=None):
    return bp.BackendState(
        name=name,
        kind=bp.BACKEND_KINDS.get(name, "unknown"),
        available=available,
        reason=reason,
    )


class _FakeProbes:
    """可编程的探测集合，顺便统计调用次数。"""

    def __init__(self, available=None, raise_on=None):
        self.available = available or {}
        self.raise_on = raise_on or set()
        self.calls = []

    def make(self):
        """策略里的探测函数是**零参**可调用，这里按名字各包一层。"""

        def probe(name):
            self.calls.append(name)
            if name in self.raise_on:
                raise RuntimeError(f"probe {name} exploded")
            return _state(name, self.available.get(name, False))

        return {name: (lambda n=name: probe(n)) for name in bp.ALL_BACKENDS}


class TestResolve(unittest.TestCase):
    """顺序解析。"""

    def setUp(self):
        self.probes = _FakeProbes()
        self.policy = bp.BackendPolicy(
            order=["lqcs", "qiskit", "offline_sim"], probes=self.probes.make()
        )

    def test_auto_uses_config_order(self):
        self.assertEqual(
            self.policy.resolve("auto"), ["lqcs", "qiskit", "offline_sim"]
        )
        self.assertEqual(self.policy.resolve(None), ["lqcs", "qiskit", "offline_sim"])

    def test_online_is_hardware_only(self):
        self.assertEqual(self.policy.resolve("online"), ["lqcs"])

    def test_offline_is_replay_only(self):
        self.assertEqual(self.policy.resolve("offline"), ["offline_sim"])

    def test_disabled_is_filtered(self):
        self.policy.set_disabled(["qiskit"])
        self.assertNotIn("qiskit", self.policy.resolve("auto"))
        self.assertEqual(self.policy.resolve("online"), ["lqcs"])

    def test_prefer_only_affects_auto(self):
        self.policy.prefer("qiskit")
        self.assertEqual(self.policy.resolve("auto")[0], "qiskit")
        # online / offline 是硬指定，不受偏好影响
        self.assertEqual(self.policy.resolve("online"), ["lqcs"])
        self.assertEqual(self.policy.resolve("offline"), ["offline_sim"])

    def test_prefer_unknown_raises(self):
        with self.assertRaises(ValueError):
            self.policy.prefer("nope")

    def test_clear_preference(self):
        self.policy.prefer("qiskit")
        self.policy.clear_preference()
        self.assertIsNone(self.policy.preferred)
        self.assertEqual(self.policy.resolve("auto")[0], "lqcs")

    def test_unknown_names_are_dropped(self):
        policy = bp.BackendPolicy(order=["lqcs", "bogus", "qiskit", "lqcs"])
        self.assertEqual(policy.order, ["lqcs", "qiskit"])

    def test_empty_order_falls_back_to_default(self):
        policy = bp.BackendPolicy(order=[])
        self.assertEqual(policy.order, list(bp.DEFAULT_ORDER))


class TestProbing(unittest.TestCase):
    """探测：异常隔离、缓存、peek 不探测。"""

    def test_probe_exception_becomes_unavailable(self):
        probes = _FakeProbes(raise_on={"qiskit"})
        policy = bp.BackendPolicy(order=["qiskit"], probes=probes.make())
        state = policy.state("qiskit")
        self.assertFalse(state.available)
        self.assertIn("探测异常", state.reason)

    def test_result_is_cached(self):
        probes = _FakeProbes(available={"qiskit": True})
        policy = bp.BackendPolicy(order=["qiskit"], probes=probes.make())
        policy.state("qiskit")
        policy.state("qiskit")
        self.assertEqual(probes.calls.count("qiskit"), 1)

    def test_force_reprobes(self):
        probes = _FakeProbes(available={"qiskit": True})
        policy = bp.BackendPolicy(order=["qiskit"], probes=probes.make())
        policy.state("qiskit")
        policy.state("qiskit", force=True)
        self.assertEqual(probes.calls.count("qiskit"), 2)

    def test_peek_never_probes(self):
        probes = _FakeProbes(available={"qiskit": True})
        policy = bp.BackendPolicy(order=["qiskit"], probes=probes.make())
        self.assertIsNone(policy.peek_active("auto"))
        self.assertEqual(probes.calls, [])

    def test_peek_uses_cache_only(self):
        probes = _FakeProbes(available={"qiskit": True})
        policy = bp.BackendPolicy(order=["qiskit"], probes=probes.make())
        policy.state("qiskit")
        self.assertEqual(policy.peek_active("auto"), "qiskit")

    def test_active_picks_first_available(self):
        probes = _FakeProbes(available={"qiskit": True})
        policy = bp.BackendPolicy(
            order=["lqcs", "qiskit", "offline_sim"], probes=probes.make()
        )
        self.assertEqual(policy.active("auto"), "qiskit")
        self.assertFalse(policy.is_hardware("auto"))

    def test_active_none_when_all_unavailable(self):
        probes = _FakeProbes()
        policy = bp.BackendPolicy(
            order=["lqcs", "qiskit", "offline_sim"], probes=probes.make()
        )
        self.assertIsNone(policy.active("auto"))

    def test_report_shape(self):
        probes = _FakeProbes(available={"qiskit": True})
        policy = bp.BackendPolicy(
            order=["lqcs", "qiskit", "offline_sim"], probes=probes.make()
        )
        report = policy.report("auto")
        self.assertEqual(report["active"], "qiskit")
        self.assertFalse(report["hardware"])
        self.assertEqual([b["name"] for b in report["backends"]],
                         ["lqcs", "qiskit", "offline_sim"])
        self.assertIn("checked_at", report)


class TestBuiltinProbes(unittest.TestCase):
    """内置探测函数。"""

    def test_probe_lqcs_without_lqms(self):
        # 这台机器上要么装了 lqms（真机栈）要么没装（演示机）。
        # 没装时必须是"不可用 + 说清楚原因"，不能抛异常。
        state = bp.probe_lqcs()
        self.assertEqual(state.name, "lqcs")
        if not bp.module_available("lqms"):
            self.assertFalse(state.available)
            self.assertIn("lqms", state.reason)

    def test_probe_qiskit_uses_injected_status(self):
        state = bp.probe_qiskit(lambda: {"available": True, "versions": {"qiskit": "2.6.2"}})
        self.assertTrue(state.available)
        self.assertIsNone(state.reason)
        self.assertEqual(state.detail["versions"]["qiskit"], "2.6.2")

    def test_probe_qiskit_unavailable_uses_hint(self):
        state = bp.probe_qiskit(
            lambda: {"available": False, "error": None, "install_hint": "pip install qiskit"}
        )
        self.assertFalse(state.available)
        self.assertIn("pip install qiskit", state.reason)

    def test_probe_qiskit_handles_exception(self):
        def boom():
            raise ValueError("kaboom")

        state = bp.probe_qiskit(boom)
        self.assertFalse(state.available)
        self.assertIn("探测失败", state.reason)

    def test_probe_offline_sim(self):
        ok = bp.probe_offline_sim(True, data_path="data/offline_data")
        self.assertTrue(ok.available)
        bad = bp.probe_offline_sim(False, data_path="data/offline_data")
        self.assertFalse(bad.available)
        self.assertIn("data/offline_data", bad.reason)


class TestServiceIntegration(unittest.TestCase):
    """服务层：策略如何影响 /execute 与 /qubits。"""

    @classmethod
    def setUpClass(cls):
        from services.quantum_service.server import QuantumService

        cls._service = QuantumService(port=3099)
        cls._original_probes = dict(cls._service._policy._probes)

    @classmethod
    def tearDownClass(cls):
        svc = cls._service
        svc._policy._probes = cls._original_probes
        for mode in ("auto",):
            svc._set_mode(mode)
        try:
            svc.stop()
        except Exception:
            pass
        try:
            svc._writer.stop()
        except Exception:
            pass

    def _install_fake_probes(self, available):
        probes = _FakeProbes(available=available)
        self._service._policy._probes = probes.make()
        self._service._policy.invalidate()
        return probes

    def test_backends_endpoint_reports_all(self):
        self._install_fake_probes({"qiskit": True})
        res = self._service.handle_request("GET", "/backends", {}, {})
        self.assertTrue(res.get("success"))
        self.assertEqual(res["active"], "qiskit")
        self.assertFalse(res["hardware"])
        names = [b["name"] for b in res["backends"]]
        self.assertIn("lqcs", names)
        self.assertIn("qiskit", names)

    def test_backends_prefer_invalid_name(self):
        res = self._service.handle_request("POST", "/backends", {"prefer": "nope"}, {})
        self.assertFalse(res.get("success", False))
        self.assertIn("choosable", res)

    def test_backends_prefer_then_clear(self):
        self._install_fake_probes({"qiskit": True})
        res = self._service.handle_request("POST", "/backends", {"prefer": "qiskit"}, {})
        self.assertEqual(res["preferred"], "qiskit")
        res = self._service.handle_request("POST", "/backends", {"prefer": "auto"}, {})
        self.assertIsNone(res["preferred"])

    def test_health_exposes_backend_without_probing(self):
        self._install_fake_probes({"qiskit": True})
        res = self._service.handle_request("GET", "/health", {}, {})
        self.assertIn("backends", res)
        self.assertIn("backend_active", res)
        self.assertIn("execution_available", res)

    def test_qubits_demo_only_when_qiskit_active(self):
        self._install_fake_probes({"qiskit": True})
        self._service._set_mode("auto")
        res = self._service.handle_request("GET", "/qubits", {}, {})
        self.assertEqual(res["source"], "demo")
        self.assertEqual(res["count"], 5)
        self.assertEqual([q["name"] for q in res["qubits"]], ["q0", "q1", "q2", "q3", "q4"])

    def test_qubits_not_demo_in_online_mode(self):
        # 真机掉线时不能拿假比特糊弄现场人员
        self._install_fake_probes({})
        self._service._set_mode("online")
        # 换成假的连接函数，免得真去连 LabRAD（这台机器上会等到超时）
        original = self._service._ensure_connected
        self._service._ensure_connected = lambda: False
        try:
            res = self._service.handle_request("GET", "/qubits", {}, {})
            self.assertNotEqual(res["source"], "demo")
            self.assertEqual(res["count"], 0)
        finally:
            self._service._ensure_connected = original
            self._service._set_mode("auto")

    def test_execute_falls_back_when_experiment_not_in_qiskit(self):
        self._install_fake_probes({"qiskit": True})
        self._service._set_mode("auto")
        res = self._service.handle_request(
            "POST", "/execute", {"code": "sq.not_an_experiment(q0)"}, {}
        )
        self.assertNotEqual(res.get("backend"), "qiskit")

    def test_execute_non_sq_code_does_not_raise(self):
        self._install_fake_probes({"qiskit": True})
        self._service._set_mode("auto")
        res = self._service.handle_request("POST", "/execute", {"code": "x = 1"}, {})
        self.assertIn("status", res)


class TestConfig(unittest.TestCase):
    """配置文件里的 order 必须是已知后端名，且默认包含 qiskit。"""

    def test_fallback_config_order(self):
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "fallback_config.json"
        )
        cfg = json.loads(path.read_text(encoding="utf-8"))
        order = cfg["quantum_service"]["backends"]["order"]
        for name in order:
            self.assertIn(name, bp.ALL_BACKENDS, f"未知后端名：{name}")
        self.assertIn("qiskit", order, "演示机要能自动落到 qiskit")

    def test_demo_qubits_file(self):
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "config" / "demo_qubits.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(raw.get("enabled"))
        names = [q["name"] for q in raw["qubits"]]
        self.assertEqual(names, ["q0", "q1", "q2", "q3", "q4"])
        self.assertIn("note", raw)


if __name__ == "__main__":
    unittest.main()

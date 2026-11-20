"""qubitclient_service 的配置自愈 + 热重载单测 —— 2026-11-20。

背景（真踩到的坑）：`config/qubitclient.json` 是 **gitignored 的本机文件**，
第一次启动时它还不存在，服务就永久停在 `initialized=false`；之后把文件补好、
填上真实 key，`/health` 依旧 503、VLM 依旧 500 —— 因为 `_load_config()` 只在
`before_start()` 里跑过一次，只能靠重启进程。

另外 `qubitclient` 是 `vendor/QubitClient` 里的**源码副本**，没 `pip install -e`
时 `from qubitclient.llm import ...` 直接 ModuleNotFoundError，表现为
"配置明明对了还是跑不通"。

本测试锁住三条：

1. `_ensure_loaded()` 只在没初始化时才重读（初始化成功后不再重复读盘）
2. `/reload` 强制重读，并把旧 client 丢掉（换 key / 换 model 不用重启）
3. 模块导入时会把 `vendor/QubitClient` 挂上 `sys.path`

跑法：
    python -m pytest tests/test_qubitclient_service_reload.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.qubitclient_service import server as qc_server  # noqa: E402


def _bare_service():
    """不跑 `__init__`（BaseService 会建目录、可能连东西），只装配状态。"""
    svc = object.__new__(qc_server.QubitClientService)
    svc._qubit_llm = object()          # 假装有个旧 client，用于验证 /reload 会丢掉它
    svc._llm_config = {}
    svc._initialized = False
    svc._init_error = None
    return svc


class TestEnsureLoaded(unittest.TestCase):
    def test_retries_when_not_initialized(self):
        svc = _bare_service()
        calls = []

        def fake_load(self):
            calls.append(1)
            self._initialized = True
            self._llm_config = {"model": "m"}

        original = qc_server.QubitClientService._load_config
        qc_server.QubitClientService._load_config = fake_load
        try:
            svc._ensure_loaded()
            self.assertEqual(len(calls), 1, "没初始化时应该重读一次")
            svc._ensure_loaded()
            self.assertEqual(len(calls), 1, "已初始化就不该再读盘")
        finally:
            qc_server.QubitClientService._load_config = original

    def test_no_retry_after_success(self):
        svc = _bare_service()
        svc._initialized = True
        calls = []

        original = qc_server.QubitClientService._load_config
        qc_server.QubitClientService._load_config = lambda self: calls.append(1)
        try:
            svc._ensure_loaded()
            self.assertEqual(calls, [])
        finally:
            qc_server.QubitClientService._load_config = original


class TestReload(unittest.TestCase):
    def test_reload_drops_client_and_rereads(self):
        svc = _bare_service()
        seen = []

        def fake_load(self):
            seen.append(True)
            self._initialized = True
            self._llm_config = {"model": "nv-community/Ising-Calibration-1-35B-A3B"}

        original = qc_server.QubitClientService._load_config
        qc_server.QubitClientService._load_config = fake_load
        try:
            out = svc._reload_config()
        finally:
            qc_server.QubitClientService._load_config = original

        self.assertEqual(len(seen), 1, "/reload 必须真的重读")
        self.assertIsNone(svc._qubit_llm, "旧 client 必须丢弃")
        self.assertTrue(out["ok"])
        self.assertEqual(out["service"], "qubitclient_service")

    def test_reload_route_is_wired(self):
        """`/reload` 必须挂在 handle_request 路由上（否则运维没法热加载）。"""
        src = Path(qc_server.__file__).read_text(encoding="utf-8")
        self.assertIn('elif path == "/reload":', src)
        self.assertIn("self._ensure_loaded()", src)


class TestVendorPath(unittest.TestCase):
    def test_vendor_qubitclient_on_sys_path(self):
        found = qc_server._find_vendor_qubitclient()
        self.assertIsNotNone(found, "vendor/QubitClient 应该能被自动找到")
        self.assertTrue((found / "qubitclient" / "__init__.py").exists())
        self.assertIn(str(found), [p for p in sys.path])


if __name__ == "__main__":
    unittest.main()

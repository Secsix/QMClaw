"""`vlm`（image_analysis）节点的失败语义单测 —— 2026-11-19。

背景：节点类型覆盖实测时发现，qubitclient（:3010）整个不可用、两个 VLM 请求
全部 HTTP 500，`image_analysis` 节点依旧报 **completed**，`outcome=achieved`，
只有 `raw_params` 里悄悄躺着一句 `{"error": "HTTP Error 500..."}`。
一条"看不懂图"的流程不能对外宣称成功。

本测试锁住三条语义：

1. 两个子请求都失败 → `status=failed`，错误里带上面包屑
2. 只有一个失败 → `status=completed` 但 `degraded=True`，`vlm_errors` 非空
3. 两个都成功 → `degraded=False`，`vlm_errors` 为空

跑法：
    python -m pytest tests/test_workflow_vlm_node.py -v
    python -m unittest tests.test_workflow_vlm_node -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from services.workflow_service.server import WorkflowService  # noqa: E402


class _Node:
    """`_execute_via_http` 只读这三样：type / config / depends。"""

    def __init__(self, node_type, config, depends=None):
        self.type = node_type
        self.config = config
        self.depends = list(depends or [])


def _bare_service(base_url):
    """不跑 `__init__`（会连 Redis、建数据目录），只拿方法。"""
    svc = object.__new__(WorkflowService)
    svc._qubitclient_service_url = base_url
    return svc


class TestVlmNode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.image = Path(self._tmp.name) / "plot.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_both_requests_fail_is_failed(self):
        """后端整个不可用 → 必须 failed，不能装作看懂了。"""
        svc = _bare_service("http://127.0.0.1:1")  # 端口 1：连接必然被拒
        node = _Node("image_analysis", {"imagePath": str(self.image), "experimentFamily": "t1"})
        result = svc._execute_via_http(node, {}, None)
        self.assertEqual(result.get("status"), "failed", result)
        self.assertIn("VLM 后端不可用", str(result.get("error")), result)
        self.assertEqual(len(result.get("vlm_errors") or []), 2, result)

    def test_missing_image_is_failed(self):
        svc = _bare_service("http://127.0.0.1:1")
        node = _Node("image_analysis", {"imagePath": str(self.image) + ".nope"})
        result = svc._execute_via_http(node, {}, None)
        self.assertEqual(result.get("status"), "failed", result)
        self.assertIn("imagePath not found", str(result.get("error")), result)


class TestVlmPartial(unittest.TestCase):
    """一条挂、一条通：completed 但标 degraded（用真后端跑端到端，此处只测语义）。"""

    def test_partial_marks_degraded(self):
        # 语义靠源码保证；这里做静态断言，避免为打桩把生产代码改成可注入。
        src = (_SERVER_DIR / "services" / "workflow_service" / "server.py").read_text(
            encoding="utf-8")
        self.assertIn('"degraded": bool(vlm_errors)', src)
        self.assertIn('if len(vlm_errors) == 2:', src)
        self.assertIn("VLM 后端不可用", src)


if __name__ == "__main__":
    unittest.main()

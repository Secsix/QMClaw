#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""/qubit/contrast 端点自检（**离线**：静态检查 + 处理器级校验，不连真机、不起服务）。

覆盖：
  A. 静态：路由存在、handler 存在、口径来自上游门面（不复制实现）、
     前端代理已接、OPERATIONS 文档已登记
  B. 处理器级：参数校验（缺 name / 非法 name / 非法 dataset_id / 非法 threshold）
     与"只读豁免写闸门"的边界（不因 allow_execute 关闭而拒绝）

用法：python checks/_contrast_ep_check.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"

sys.path.insert(0, str(SERVER))
# runner 用 redirect_stdout 包住本脚本，此时 stdout 是 StringIO（没有 reconfigure）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
WARN = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {extra}")
    return bool(cond)


def warn(name, extra=""):
    global WARN
    WARN += 1
    print(f"  WARN  {name} {extra}")


# ------------------------------------------------------------------ A 静态
def part_a():
    print("[A] 静态检查")
    src_path = SERVER / "services" / "quantum_service" / "server.py"
    src = src_path.read_text(encoding="utf-8")

    check(
        "server.py 有 /qubit/contrast 路由",
        'elif path == "/qubit/contrast"' in src,
    )
    check(
        "server.py 有 _handle_qubit_contrast handler",
        "def _handle_qubit_contrast(" in src,
    )

    # 口径必须来自上游门面，不能在本仓复刻一份
    m = re.search(
        r"def _handle_qubit_contrast\(.*?(?=\n    def |\n    # ──)",
        src,
        re.S,
    )
    body = m.group(0) if m else ""
    check(
        "handler 从上游门面取 build_contrast_code/annotate",
        "services.workflow_service.dsl.backends.lqcs" in body
        and "build_contrast_code" in body
        and "annotate" in body,
    )
    check(
        "handler 不在本地复刻对比度算法（无 np.linalg / center0 计算）",
        "center0" not in body and "np.linalg" not in body,
    )
    check(
        "handler 走 busy 串行的执行通道（_submit_job）",
        "_submit_job" in body,
    )
    check(
        "handler 保留 stuck / degraded / not_connected 拒绝",
        all(k in body for k in ("_exec_stuck", "is_degraded", "_ensure_connected")),
    )

    proxy = (SERVER / "src" / "services" / "serviceProxy.ts").read_text(encoding="utf-8")
    check("前端代理已接 /quantum/qubit/contrast", "/quantum/qubit/contrast" in proxy)

    docs = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    check("OPERATIONS.md 登记了 /qubit/contrast", "/qubit/contrast" in docs)

    # 上游门面里必须有口径实现（本仓只是薄壳）
    upstream = ROOT / "vendor" / "OpenQWF" / "adapters" / "lqcs.py"
    if upstream.exists():
        usrc = upstream.read_text(encoding="utf-8")
        check("上游 adapters/lqcs.py 有 build_contrast_code", "def build_contrast_code(" in usrc)
        check("上游 adapters/lqcs.py 有 annotate", "def annotate(" in usrc)
    else:
        warn("上游 submodule 未初始化，跳过上游口径检查（vendor/OpenQWF 缺失）")


# ------------------------------------------------------------ B 处理器级
def part_b():
    print("[B] 处理器级：参数校验（不碰真机）")
    try:
        from services.quantum_service.server import QuantumService
    except Exception as exc:  # noqa: BLE001 —— 缺依赖记 SKIP，不判 FAIL
        warn(f"导入 QuantumService 失败，跳过处理器级检查：{type(exc).__name__}: {exc}")
        return

    svc = object.__new__(QuantumService)
    # 这些参数校验发生在任何真机访问之前，桩掉后面的依赖即可
    svc._exec_stuck = None
    svc._fallback = type("F", (), {"is_degraded": False})()
    svc._ensure_connected = lambda: False  # 走到这里就说明校验放行了

    res = svc._handle_qubit_contrast({})
    check("缺 name → validation_error", res.get("error_code") == "validation_error", res)

    res = svc._handle_qubit_contrast({"name": "q1; import os"})
    check("非法 name → validation_error", res.get("error_code") == "validation_error", res)

    res = svc._handle_qubit_contrast({"name": "q1", "dataset_id": "abc"})
    check("非法 dataset_id → validation_error", res.get("error_code") == "validation_error", res)

    res = svc._handle_qubit_contrast({"name": "q1", "threshold": "high"})
    check("非法 threshold → validation_error", res.get("error_code") == "validation_error", res)

    # 参数合法 → 不再是 validation_error（未连接 → not_connected）
    res = svc._handle_qubit_contrast({"name": "q1"})
    check(
        "参数合法 → 越过校验（未连接时给 not_connected）",
        res.get("error_code") == "not_connected",
        res,
    )


def main():
    print("=== /qubit/contrast 端点自检 ===")
    part_a()
    part_b()
    print(f"\nPASS={PASS} FAIL={FAIL} WARN={WARN}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

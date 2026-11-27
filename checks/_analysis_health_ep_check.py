#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""/health/report 端点自检（**离线**：静态检查 + 处理器级校验，不连真机、不起服务）。

覆盖：
  A. 静态：路由/handler 存在、只读离线（不要求 LabRAD 连接）、
     体检实现已下沉到 analysis_service、tools 侧只剩 HTTP 客户端薄壳、
     前端代理与 OPERATIONS 文档已登记
  B. 处理器级：参数校验（缺 vault_dir / 目录不存在 / start 非整数）
     与正常路径的返回结构（count / alive / rows / csv / markdown）

用法：python checks/_analysis_health_ep_check.py
"""
import re
import sys
import tempfile
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
    src = (SERVER / "services" / "analysis_service" / "server.py").read_text(encoding="utf-8")

    check(
        "analysis_service/server.py 有 /health/report 路由",
        'elif path == "/health/report"' in src,
    )
    check(
        "analysis_service/server.py 有 _handle_health_report handler",
        "def _handle_health_report(" in src,
    )

    m = re.search(r"def _handle_health_report\(.*?(?=\n    def )", src, re.S)
    body = m.group(0) if m else ""
    check(
        "handler 不要求 LabRAD 连接（离线只读）",
        body and "_ensure_connected" not in body and "is_connected" not in body,
    )
    check(
        "handler 先校验参数再计算（validation_error）",
        body and "validation_error" in body and "error_envelope" in body,
    )
    check(
        "handler 用服务内的体检模块（不自己算指标）",
        "from .health_report import" in body,
    )

    # 体检实现必须在服务端
    for mod in ("datavault.py", "vault_metrics.py", "health_report.py"):
        check(
            f"体检实现已下沉：server/services/analysis_service/{mod}",
            (SERVER / "services" / "analysis_service" / mod).is_file(),
        )

    # tools 侧只允许留客户端薄壳
    tool = ROOT / "tools" / "qubit_health_report" / "health_report.py"
    check("tools 侧保留客户端薄壳 health_report.py", tool.is_file())
    if tool.is_file():
        tsrc = tool.read_text(encoding="utf-8")
        check(
            "薄壳不自己读 HDF5（无 h5py / decode_labrad_value）",
            "h5py" not in tsrc and "decode_labrad_value" not in tsrc,
        )
        check(
            "薄壳不 import numpy/scipy（指标口径不在工具层）",
            "import numpy" not in tsrc and "import scipy" not in tsrc,
        )
        check(
            "薄壳通过 HTTP 端点取结果",
            "/health/report" in tsrc and "urllib.request" in tsrc,
        )
    for stale in ("dvread.py", "metrics.py"):
        check(
            f"tools 侧已删除 {stale}（实现归服务端）",
            not (ROOT / "tools" / "qubit_health_report" / stale).exists(),
        )

    proxy = (SERVER / "src" / "services" / "serviceProxy.ts").read_text(encoding="utf-8")
    check("前端代理已接 /analysis/health/report", "/analysis/health/report" in proxy)

    docs = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    check("OPERATIONS.md 登记了 /health/report", "/health/report" in docs)


# ------------------------------------------------------------ B 处理器级
def part_b():
    print("[B] 处理器级：参数校验与返回结构（不碰真机）")
    try:
        from services.analysis_service.server import AnalysisService
    except Exception as exc:  # noqa: BLE001 —— 缺依赖记 SKIP，不判 FAIL
        warn(f"导入 AnalysisService 失败，跳过处理器级检查：{type(exc).__name__}: {exc}")
        return

    svc = object.__new__(AnalysisService)

    res = svc._handle_health_report({})
    check("缺 vault_dir → validation_error", res.get("error_code") == "validation_error", res)

    res = svc._handle_health_report({"vault_dir": "Z:/definitely/not/here"})
    check("目录不存在 → validation_error", res.get("error_code") == "validation_error", res)

    with tempfile.TemporaryDirectory() as tmp:
        res = svc._handle_health_report({"vault_dir": tmp, "start": "abc"})
        check("start 非整数 → validation_error", res.get("error_code") == "validation_error", res)

        # 正常路径：用假 analyze 顶掉真读盘，校验返回结构
        from services.analysis_service import health_report as hr

        fake_rows = [
            {
                "qubit": "q0",
                "alive": True,
                "iqraw_ok": True,
                "piamp_ok": True,
                "iqraw_snr": 0.5,
                "iqraw_norm_best": 0.6,
                "rabi_contrast": 0.4,
                "rabi_amp_opt": 0.35,
                "wp_zpa": 0.1,
                "wp_f10": 6.1,
                "wp_f21": 5.9,
                "wp_fread": 6.0,
                "wp_fc": 6.2,
                "wp_fc_read": 6.25,
                "n_files": 2,
                "iqraw_best_index": 1,
                "rabi_index": 2,
                "wp_regs": {"f10": 6.1},
                "Ramsey_df_valid": False,
            }
        ]
        original = hr.analyze
        hr.analyze = lambda vault, start=None, end=None: fake_rows  # type: ignore[assignment]
        try:
            res = svc._handle_health_report({"vault_dir": tmp, "start": 1, "end": 2})
        finally:
            hr.analyze = original  # type: ignore[assignment]

        check("正常路径 → success", res.get("success") is True, res)
        check("返回 count / alive", res.get("count") == 1 and res.get("alive") == 1, res)
        check("返回 rows（可 JSON 化）", isinstance(res.get("rows"), list) and res["rows"], res)
        check("返回 csv 文本", isinstance(res.get("csv"), str) and "qubit" in res.get("csv", ""), res)
        check("返回 markdown 文本", "判活" in res.get("markdown", ""), res)


def main():
    print("=== /health/report 端点自检 ===")
    part_a()
    part_b()
    print(f"\nPASS={PASS} FAIL={FAIL} WARN={WARN}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

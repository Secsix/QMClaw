"""
services/quantum_service/qiskit_runner.py — 独立解释器里的 qiskit 执行器

量子服务跑在 `lqcs` 环境（那儿有 `lqms`，是真机 LabRAD 的唯一出路），
而 qiskit 按部署约定装在**另一个** conda 环境里。两个环境缺的是彼此，
没法靠"换一个解释器跑整个服务"解决 —— 那样真机路径就废了。

所以这里提供一条**跨解释器**的窄通道：

    服务进程 ──(stdin: JSON)──> qiskit 环境的 python
             <──(argv[1] 临时文件: JSON)──

用法：config/system.json → qiskit.python_executable 指向 qiskit 环境的解释器，
qiskit_backend 就自动改走子进程；不配则进程内直接跑（qiskit 装在服务环境里时用）。

**为什么结果走文件而不是 stdout**：`python -m services.quantum_service.qiskit_runner`
会先执行包的 `__init__.py`，那一串 import 里就有会打印东西的模块
（实测 `MemoryStore: Initialized at ...` 就是这么来的）。
stdout 上只要多一行非 JSON，父进程就没法解析了。走文件就彻底免疫。

**为什么不用常驻 worker**：常驻 worker 能省掉每次约 1.6s 的 import 开销
（实测 `import qiskit + qiskit_aer` ≈ 1.6s），但要处理进程存活、
僵尸回收、崩溃重启等一堆边界情况。现在用的是**每次请求起一个子进程**：
无状态、崩了不影响服务、超时可以直接杀掉（线程做不到）。
等实际用起来嫌慢了再换常驻 worker。
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
from typing import Any, Optional


def _json_default(obj: Any) -> Any:
    """numpy 标量/数组之类 JSON 化不了的东西，转成 Python 原生类型。"""
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except Exception:
        pass
    return str(obj)


def _emit(result: Dict[str, Any], out_path: Optional[str]) -> None:
    """把结果 JSON 写出去。

    有 out_path 就写文件（父进程指定的临时文件）；没有就退回 stdout，
    方便手工调试时直接 `echo '{...}' | python qiskit_runner.py` 看输出。
    """
    text = json.dumps(result, ensure_ascii=False, default=_json_default)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else None

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except Exception as exc:  # noqa: BLE001
        _emit({
            "status": "qiskit_error",
            "error": f"请求 JSON 解析失败: {exc}",
        }, out_path)
        return 2

    real_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured

    result: Dict[str, Any] = {}
    try:
        # 强制进程内执行：子进程里再按配置走子进程会无限套娃。
        os.environ["QMCLAW_QISKIT_INPROCESS"] = "1"
        from services.quantum_service.qiskit_backend import run_in_process
        result = run_in_process(payload)
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "qiskit_error",
            "error": f"{type(exc).__name__}: {exc}",
            "stderr": traceback.format_exc(),
        }
    finally:
        sys.stdout = real_stdout

    noise = captured.getvalue()
    if noise:
        # qiskit/Aer 的零散输出：只在出错时并进 stderr，正常时丢掉，
        # 免得污染给用户看的 stdout。
        if result.get("error"):
            result["stderr"] = f"{result.get('stderr', '')}\n[runner stdout]\n{noise}".strip()
        else:
            result["runner_output"] = noise[-2000:]

    _emit(result, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

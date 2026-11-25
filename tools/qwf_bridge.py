#!/usr/bin/env python
"""QWF 薄壳桥接层 —— 把仓根的 qwf_gen / qwf_run / qwf_lint 接到上游实现上。

上游实现在私有仓 `Secsix/OpenQWF`（本仓以 submodule 引用 `vendor/OpenQWF/`），
仓根只保留三个**薄壳**入口（`qwf_gen.py` / `qwf_run.py` / `qwf_lint.py`），壳里做三件事：

1. **委派**：按文件路径加载上游 `tools/<name>.py`
   （上游 `tools/` 不是 Python 包，没有 `__init__.py`，只能用 `importlib` 按路径加载）
2. **环境**：把 `QWF_CONFIG_DIR` / `QWF_VOCAB_PATH` / `QWF_DATA_DIR` 指向本仓 `server/`
   （上游缺省会落到 submodule 里的旧 config 副本，`qwf/paths.py` 会去读它）
3. **补丁**：本仓自己的三处语义
   - `budget.max_datasets` 恒写（上游只在 goal 模式写；lint 的 E_NO_BUDGET
     要求 max_wall / max_writes / max_datasets 三个一起声明）
   - `get()` 非 2xx 也读 body（上游没有 HTTPError 分支，见 `patch_get` 注释）
   - 授权留底 / RunRecord 目录从 submodule 挪到本仓 `server/data/`（见 `retarget_server_paths`）

**改实现一律改上游仓**，本仓只同步 submodule 指针（细则见 `docs/OPENQWF_SYNC.md`）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 仓根 / submodule / 本仓后端目录
REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor" / "OpenQWF"
VENDOR_TOOLS = VENDOR_DIR / "tools"
SERVER_ROOT = REPO_ROOT / "server"
CONFIG_DIR = SERVER_ROOT / "config"

ENV_CONFIG_DIR = "QWF_CONFIG_DIR"
ENV_VOCAB_PATH = "QWF_VOCAB_PATH"
ENV_DATA_DIR = "QWF_DATA_DIR"

SUBMODULE_HINT = (
    "上游 OpenQWF submodule 不完整（找不到 {path}）。\n"
    "  先跑：git submodule update --init --recursive vendor/OpenQWF"
)


class _StdoutGuard:
    """临时替身：上游 `qwf_run.py` 在 import 时就裸调 `sys.stdout.reconfigure(...)`。

    自检 runner 用 runpy + StringIO 接管道时 stdout 没有这个方法，
    不能让它变成 import 失败的原因。退出上下文时恢复原 stdout。
    """

    def __init__(self, real):
        self._real = real
        self._saved = None

    def reconfigure(self, *args, **kwargs):  # noqa: D102 - 故意吞掉
        return None

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        self._saved = sys.stdout
        if not hasattr(sys.stdout, "reconfigure"):
            sys.stdout = self
        return self

    def __exit__(self, *exc):
        sys.stdout = self._saved
        return False


def setup_env() -> None:
    """把上游 `qwf` 包挂进 sys.path，并把配置/数据目录指向本仓 `server/`。

    必须在加载上游模块**之前**调用：上游模块在 import 期就会算路径。
    """
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    if str(VENDOR_TOOLS) not in sys.path:
        # 上游 qwf_run.py 的 `_verify()` 会 `import e1_verify`（上游 tools/ 同目录）
        sys.path.append(str(VENDOR_TOOLS))
    os.environ.setdefault(ENV_CONFIG_DIR, str(CONFIG_DIR))
    os.environ.setdefault(ENV_VOCAB_PATH, str(CONFIG_DIR / "experiment_configs.json"))
    os.environ.setdefault(ENV_DATA_DIR, str(SERVER_ROOT / "data" / "workflow-runs"))


def load_tool(name: str):
    """按路径加载上游 `vendor/OpenQWF/tools/<name>`，返回模块对象。"""
    setup_env()
    path = VENDOR_TOOLS / name
    if not path.is_file():
        raise SystemExit(SUBMODULE_HINT.format(path=path))
    spec = importlib.util.spec_from_file_location("_openqwf_" + path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(SUBMODULE_HINT.format(path=path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with _StdoutGuard(sys.stdout):
        spec.loader.exec_module(module)
    return module


def patch_get(module) -> None:
    """把上游 `get()` 换成「非 2xx 也读 body」的版本。

    上游只在 2xx 时返回 body。quantum_service 在没有 LabRAD 时 `/health` 会返回
    503 + 一份完整的降级报告（`backend_active: qiskit` 等），那是**有用信息**，
    不是故障。让 urllib 抛 HTTPError 会让整个 qwf_run 在打印状态那一步就崩掉，
    压根走不到干跑/真跑。
    """
    def get(base, path, timeout=30):
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": False, "status": exc.code, "error": raw[:400]}

    module.get = get


def retarget_server_paths(module) -> None:
    """把上游脚本算目录用的 `Path(__file__).resolve().parents[1]` 指向本仓 `server/`。

    上游 `qwf_run.py` 用它算两处位置：
    - `_write_audit()`：授权留底 → `<root>/data/audit/e1_authorizations.jsonl`
    - `_verify()`：RunRecord → `<root>/data/workflow-runs`

    上游脚本在 `vendor/OpenQWF/tools/` 下，`parents[1]` 会落到 submodule 里
    （把运行数据写进 submodule，还会弄脏 submodule 的工作区）。
    本仓这两个目录都在 `server/data/` 下（已被 .gitignore 忽略），所以把
    `__file__` 换成一个落在 `server/` 下的替身路径 —— 只影响算目录，不影响别的。

    注意：模块级 `REPO_ROOT`（上游 import 期算的，用于 `from qwf.manifest import …`）
    在本函数之前就已按真实路径算好，仍指向 `vendor/OpenQWF`，不受影响。
    """
    module.__file__ = str(SERVER_ROOT / "tools" / "qwf_run.py")

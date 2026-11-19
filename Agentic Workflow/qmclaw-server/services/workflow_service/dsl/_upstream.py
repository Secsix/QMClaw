"""上游 OpenQWF 桥接 —— 把 vendor 副本接成本地 `dsl` 门面的实现层。

背景
----
DSL 的**实现**已经迁到独立仓 `Secsix/OpenQWF`（`qwf/` 核心 + `adapters/` 适配器）。
本仓只保留一份**逐字副本**：`vendor/openqwf/`（由 `tools/sync_openqwf.py` 同步，
`tools/check_openqwf_drift.py` 负责查漂移）。本模块负责把那份副本接上。

两件事必须做，否则上游模块会"找不到家"：

1. **`sys.path`**：上游按"独立仓"布局写，`qwf/` 与 `adapters/` 是**顶层包**
   （`adapters/lqcs.py` 里 `from qwf.const import ...`）。不把
   `vendor/openqwf` 挂上去，`import qwf` 就会失败。
2. **路径环境变量**：上游 `qwf/paths.py` 默认去 `<仓根>/config/` 找配置。
   vendor 副本的上一级是 `vendor/`，那里**没有** `config/`（真机标定参数、
   词表、阈值全在 `qmclaw-server/config/`）。所以要注入：

   | 环境变量 | 本仓取值 | 上游默认 |
   |---|---|---|
   | `QWF_CONFIG_DIR` | `<server>/config` | `<仓根>/config` |
   | `QWF_VOCAB_PATH` | `<server>/config/experiment_configs.json` | `<config_dir>/experiment_configs.json` |
   | `QWF_DATA_DIR` | `<server>/data/workflow-runs` | `<config_dir 同级>/data` |

   规则：**不设环境变量时上游行为与原来完全一致**（见 `qwf/paths.py` 的模块
   文档），所以这里只在"没设"时补默认值，不覆盖调用方的显式设置。

`QWF_DATA_DIR` 取 `<server>/data/workflow-runs`（而不是 `<server>/data`）：
本地 `server.py` 的 `DATA_DIR` 就是这个路径，上游 `qwf/host.py` 的兜底落档
目录也读同一个变量 —— 两边必须落在同一个地方。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

#: 环境变量名（与上游 `qwf/paths.py` 的常量一致，别各写各的）
ENV_CONFIG_DIR = "QWF_CONFIG_DIR"
ENV_VOCAB_PATH = "QWF_VOCAB_PATH"
ENV_DATA_DIR = "QWF_DATA_DIR"

#: vendor 副本相对仓库根的位置
_VENDOR_RELPATH = ("vendor", "openqwf")

_ready = False


def repo_root() -> Path:
    """本仓根目录（向上找 `vendor/openqwf/qwf/__init__.py`）。

    不写死层数：`Agentic Workflow/qmclaw-server/...` 这种嵌套以后可能变，
    认标记文件比认层级稳。
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent.joinpath(*_VENDOR_RELPATH, "qwf", "__init__.py")).exists():
            return parent
    raise ImportError(
        f"找不到上游副本（{'/'.join(_VENDOR_RELPATH)}）：从 {here} 向上没找到带 "
        f"qwf/__init__.py 的目录。先跑 tools/sync_openqwf.py 同步上游。"
    )


def vendor_dir() -> Path:
    """上游副本目录（`vendor/openqwf`）。"""
    return repo_root().joinpath(*_VENDOR_RELPATH)


def server_root() -> Path:
    """`qmclaw-server` 根目录（本文件在 `services/workflow_service/dsl/` 下，上三级）。"""
    return Path(__file__).resolve().parents[3]


def setup() -> None:
    """把上游副本接上：挂 `sys.path` + 注入路径环境变量（幂等）。

    必须在**任何** `import qwf` 之前调用：上游 `qwf/metrics.py`、
    `qwf/autorevert.py` 在 import 时就会算配置路径。
    """
    global _ready
    if _ready:
        return
    vdir = vendor_dir()
    if not (vdir / "qwf" / "__init__.py").exists():
        raise ImportError(
            f"上游副本不完整：{vdir} 下没有 qwf/__init__.py。"
            "先跑 python tools/sync_openqwf.py --from <上游目录>。"
        )
    if str(vdir) not in sys.path:
        sys.path.insert(0, str(vdir))
    server = server_root()
    # 只补默认值：调用方显式设置的环境变量优先。
    os.environ.setdefault(ENV_CONFIG_DIR, str(server / "config"))
    os.environ.setdefault(ENV_VOCAB_PATH, str(server / "config" / "experiment_configs.json"))
    os.environ.setdefault(ENV_DATA_DIR, str(server / "data" / "workflow-runs"))
    # `services.common.*` 的回退导入需要服务根在 sys.path 上（上游 registry 的兜底路径）。
    if str(server) not in sys.path:
        sys.path.append(str(server))
    _ready = True


def upstream(name: str) -> ModuleType:
    """导入上游模块（先确保桥接就绪）。

    `name` 用上游的模块路径，例如 `"qwf.const"`、`"adapters.lqcs"`。
    """
    setup()
    return importlib.import_module(name)


__all__ = [
    "ENV_CONFIG_DIR",
    "ENV_VOCAB_PATH",
    "ENV_DATA_DIR",
    "repo_root",
    "vendor_dir",
    "server_root",
    "setup",
    "upstream",
]

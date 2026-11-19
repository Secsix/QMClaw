"""门面：统一的 LLM 调用 —— 实现见上游 `vendor/openqwf/qwf/llm.py`。

与其他门面模块的**唯一差别**：`server_root()` 保留**本地**语义。

上游 `qwf/llm.py` 的 `server_root()` 返回的是"上游仓根"（独立仓布局下是
`qwf/` 的上一级）。本仓把上游**逐字 vendor** 进来后，那个"上一级"是
`vendor/` —— 不是 `qmclaw-server`，配置也就找错了地方。所以这里显式覆写：
`server_root()` 仍然返回 `qmclaw-server/`（与迁移前一致）。

配置读取本身不受影响：上游 `load_llm_config()` 走 `qwf.paths.config_path()`
（`QWF_CONFIG_DIR` 注入 → `<server>/config/qubitclient.json`），
与迁移前读的是同一个文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from qwf.llm import DEFAULT_TIMEOUT, chat, extract_json, load_llm_config  # noqa: F401

__all__ = ["load_llm_config", "chat", "extract_json", "server_root", "DEFAULT_TIMEOUT"]


def server_root(root: Optional[Path] = None) -> Path:
    """`services/workflow_service/dsl/llm.py` → `qmclaw-server/`。

    路径层级：dsl → workflow_service → services → qmclaw-server（parents[3]）。
    """
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parents[3]

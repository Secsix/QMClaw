"""配置 / 数据目录解析（纯标准库，无第三方依赖）。

为什么要这一层：上游 `qwf/` 原本把配置路径**写死**在各个模块里
（`Path(__file__).resolve().parents[1] / "config"`）。独立仓里这样能跑，但一旦
`qwf/` 被**逐字 vendor** 进下游（`vendor/openqwf/qwf/`），写死的路径就会指向
vendor 副本的上一级 —— 那里**没有** `config/`，也没有真机标定参数。

于是路径必须**可注入**：

| 环境变量 | 覆盖对象 | 默认值 |
|---|---|---|
| `QWF_CONFIG_DIR` | 配置目录 | 仓根 `config/` |
| `QWF_VOCAB_PATH` | 实验词表 JSON | `<config_dir>/experiment_configs.json` |
| `QWF_DATA_DIR`  | 运行数据目录 | `<config_dir 同级>/data/` |

规则（**不设环境变量时与原来完全一致**）：

* `config_dir()` → 仓根 `config/`（`qwf/paths.py` 的上一级就是仓根）；
* `vocab_path()` → `config_dir()/experiment_configs.json`；
* `data_dir()` → `config_dir()` **同级**的 `data/`（仓根 `data/`；
  下游注入 `QWF_CONFIG_DIR=<server>/config` 时自动变成 `<server>/data`）。

用法：

    from .paths import config_dir, config_path, vocab_path, data_dir
    config_path("lab_criteria.json")   # '<config_dir>/lab_criteria.json'
"""
from __future__ import annotations

import os
from pathlib import Path

#: 环境变量名（对外公开，门面与文档都引用这三个常量）
ENV_CONFIG_DIR = "QWF_CONFIG_DIR"
ENV_VOCAB_PATH = "QWF_VOCAB_PATH"
ENV_DATA_DIR = "QWF_DATA_DIR"

#: 词表文件名（放在配置目录下）
VOCAB_FILENAME = "experiment_configs.json"


def repo_root() -> str:
    """本仓根目录（`qwf/` 的上一级）。

    独立仓布局：`qwf/paths.py` → `qwf/` → 仓根。
    vendor 到下游后，这个值指向 vendor 副本的上一级 —— 那时**必须**用环境变量
    注入真实目录，见模块文档。
    """
    return str(Path(__file__).resolve().parent.parent)


def _abs(path: str) -> str:
    """转绝对路径（不要求存在）。"""
    return os.path.abspath(os.path.expanduser(path))


def config_dir() -> str:
    """配置目录。

    `QWF_CONFIG_DIR` 优先；否则仓根 `config/`。
    """
    env = str(os.environ.get(ENV_CONFIG_DIR) or "").strip()
    if env:
        return _abs(env)
    return os.path.join(repo_root(), "config")


def config_path(*parts: str) -> str:
    """配置目录下的相对路径（`config_path("lab_criteria.json")`）。"""
    if not parts:
        return config_dir()
    return os.path.join(config_dir(), *[str(p) for p in parts])


def vocab_path() -> str:
    """实验词表 JSON 路径。

    `QWF_VOCAB_PATH` 优先；否则 `config_dir()/experiment_configs.json`。
    """
    env = str(os.environ.get(ENV_VOCAB_PATH) or "").strip()
    if env:
        return _abs(env)
    return config_path(VOCAB_FILENAME)


def data_dir() -> str:
    """运行数据目录。

    `QWF_DATA_DIR` 优先；否则 `config_dir()` **同级**的 `data/`
    （仓根 `data/`；注入 `QWF_CONFIG_DIR=<server>/config` 时是 `<server>/data`）。
    """
    env = str(os.environ.get(ENV_DATA_DIR) or "").strip()
    if env:
        return _abs(env)
    parent = os.path.dirname(config_dir().rstrip("\\/")) or config_dir()
    return os.path.join(parent, "data")


__all__ = [
    "ENV_CONFIG_DIR",
    "ENV_VOCAB_PATH",
    "ENV_DATA_DIR",
    "VOCAB_FILENAME",
    "repo_root",
    "config_dir",
    "config_path",
    "vocab_path",
    "data_dir",
]

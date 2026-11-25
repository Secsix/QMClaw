#!/usr/bin/env python
"""qwf 离线体检 CLI —— 不起服务、不碰硬件，纯静态检查。

> 实现住在上游 OpenQWF（`vendor/OpenQWF/tools/qwf_lint.py`），本文件是**薄壳**：
> 委派上游 linter（`qwf.lint.lint_text`），并把配置目录指向本仓 `server/config`。
> 改实现请改上游仓（`Secsix/OpenQWF`）并同步 submodule 指针，见 `docs/OPENQWF_SYNC.md`。

用法：
    python D:/qmclaw/qwf_lint.py a.qwf b.qwf
    python D:/qmclaw/qwf_lint.py a.qwf --json
    python D:/qmclaw/qwf_lint.py a.qwf --strict      # 有 warning 也返回非 0

退出码：0 = 没有 error；1 = 有 error（--strict 时 warning 也算）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

import qwf_bridge  # noqa: E402

_MOD = None


def _load():
    """加载上游 linter 模块（缓存）。"""
    global _MOD
    if _MOD is None:
        _MOD = qwf_bridge.load_tool("qwf_lint.py")
    return _MOD


def lint_text(text: str):
    """对一段 .qwf 文本做静态体检（上游 `qwf.lint.lint_text`）。"""
    return _load().lint_text(text)


def main() -> int:
    return _load().main()


if __name__ == "__main__":
    # 控制台编码修正（Windows 默认 GBK，.qwf 里有中文注释时会炸）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())

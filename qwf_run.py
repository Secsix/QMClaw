#!/usr/bin/env python
"""qwf_run.py - 用 .qwf 文件跑一次量子测控实验（唯一推荐入口，2026-11-05 起）。

> 实现住在上游 OpenQWF（`vendor/OpenQWF/tools/qwf_run.py`），本文件是**薄壳**：
> 委派上游 runner，并补两条本仓语义
> （① `get()` 非 2xx 也读 body；② 授权留底 / RunRecord 落在本仓 `server/data/`）。
> 改实现请改上游仓（`Secsix/OpenQWF`）并同步 submodule 指针，见 `docs/OPENQWF_SYNC.md`。

流程与真机 D1 一致，只是入口换成任意 .qwf：
    parse_qwf(.qwf) -> validate(requireWrites) -> 成本估算 -> run_ir -> 轮询 -> 汇报

用法：
    python qwf_run.py t1_q4ld3.qwf              # 只看计划（只解析+校验）
    python qwf_run.py t1_q4ld3.qwf --dry        # 干跑（不碰硬件）
    python qwf_run.py t1_q4ld3.qwf --if yes     # 真跑（写 DataVault）

P3 额度/票据（`docs/DSL_DESIGN.md` §13.4）：
    python qwf_run.py x.qwf --request-id 20261105-a      # 归属 auto-20261105-a，默认只能跑 1 次实验
    python qwf_run.py x.qwf --create-task --experiments 3 # 新建 task（3 次实验额度）再跑
    python qwf_run.py x.qwf --task t-20261105-213000      # 用已有 task 的额度

生成 .qwf 用 `qwf_gen.py`，例：
    python qwf_gen.py --fn sq.t1 --qubit q4ld3 --param zpa=0.0 --param delay=0,84000,6000
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

import qwf_bridge  # noqa: E402

_MOD = None


def _load():
    """加载上游 runner，并打上本仓的两处补丁。

    - `patch_get`：非 2xx 也读 body（上游没有 HTTPError 分支）
    - `retarget_server_paths`：把算目录用的 `parents[1]` 从 submodule 挪到本仓 `server/`
    """
    global _MOD
    if _MOD is None:
        mod = qwf_bridge.load_tool("qwf_run.py")
        qwf_bridge.patch_get(mod)
        qwf_bridge.retarget_server_paths(mod)
        _MOD = mod
    return _MOD


def main() -> int:
    return _load().main()


if __name__ == "__main__":
    # 控制台编码修正（Windows 默认 GBK，输出里有中文会炸）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())

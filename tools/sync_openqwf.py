#!/usr/bin/env python
"""上游 OpenQWF（QWF DSL）submodule 管理。

背景
----
DSL 的**真源**在独立仓 `Secsix/OpenQWF`（私有仓）。本仓以 **submodule** 的形式
引用它（`vendor/OpenQWF`，见 `.gitmodules`）：本仓**不保存**任何源码副本，
只记录一个指针（gitlink → commit）。DSL 实现改动全部发生在上游仓。

用法
----
    python tools/sync_openqwf.py --status     # 看当前指针 / 本地 submodule 状态（默认）
    python tools/sync_openqwf.py --init       # 首次拉取（= git submodule update --init --recursive）
    python tools/sync_openqwf.py --update     # 把指针移到上游最新 main（需仓库访问权）
    python tools/sync_openqwf.py --check      # 离线自检：指针 == 本地 HEAD，且工作区干净

日常流程
--------
1. 在 `vendor/OpenQWF/` 里改上游代码 → 在**上游仓**提交并 push（push 由人做）。
2. 回到本仓：`python tools/sync_openqwf.py --update`（或直接在 submodule 里 pull
   后 `git add vendor/OpenQWF`）→ 本仓指针随之移动。
3. 本仓的提交由人做；本脚本只动 submodule 指针，不碰本仓其它文件。

约定
----
* 本仓**不**保存上游源码：任何 `vendor/OpenQWF/**` 的普通文件都不该出现在本仓索引里
  （那说明有人绕过 submodule 直接 add 了）。
* `--update` 会 checkout submodule 到上游最新 `main`；本仓指针的提交仍由人决定。
* 上游仓自己的 `.gitignore` 管它内部的运行产物，本仓 `.gitignore` 管不到 submodule 内部。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBMODULE_PATH = "vendor/OpenQWF"
SUBMODULE_DIR = REPO_ROOT / "vendor" / "OpenQWF"
UPSTREAM_REPO = "https://github.com/Secsix/OpenQWF.git"
DEFAULT_BRANCH = "main"


def submodule_dir() -> Path:
    """submodule 实际目录（兼容历史遗留的小写目录名 ``vendor/openqwf``）。"""
    if SUBMODULE_DIR.exists():
        return SUBMODULE_DIR
    legacy = REPO_ROOT / "vendor" / "openqwf"
    return legacy if legacy.exists() else SUBMODULE_DIR


# ── git 辅助 ────────────────────────────────────────────────────────────────
def _git(args, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    env = dict(os.environ)
    # 禁止交互式凭据提示：私有仓无凭据时应直接失败，而不是弹窗/写控制台文件
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def pinned_commit() -> Optional[str]:
    """本仓记录的 submodule 指针（gitlink 指向的 commit）。

    优先读 HEAD（已提交的状态）；本仓还没提交时读索引。
    """
    code, out, _ = _git(["ls-tree", "HEAD", "--", SUBMODULE_PATH])
    if code == 0 and out:
        parts = out.split()
        if len(parts) >= 3 and parts[1] == "160000":
            return parts[2]
    code, out, _ = _git(["ls-files", "-s", "--", SUBMODULE_PATH])
    if code == 0 and out:
        parts = out.split()
        if len(parts) >= 2 and parts[0] == "160000":
            return parts[1]
    return None


def local_head() -> Optional[str]:
    """本地 submodule 工作区的 HEAD（未初始化时 None）。"""
    if not (submodule_dir() / ".git").exists():
        return None
    code, out, _ = _git(["rev-parse", "HEAD"], cwd=submodule_dir())
    return out if code == 0 else None


def upstream_head(branch: str = DEFAULT_BRANCH) -> Optional[str]:
    """上游 `branch` 的最新 commit（需要仓库访问权；失败返回 None）。"""
    code, out, _ = _git(
        ["ls-remote", "--heads", UPSTREAM_REPO, f"refs/heads/{branch}"],
        cwd=REPO_ROOT,
    )
    if code != 0 or not out:
        return None
    return out.split()[0]


def local_dirty() -> bool:
    """submodule 工作区是否有未提交改动。"""
    if not (submodule_dir() / ".git").exists():
        return False
    code, out, _ = _git(["status", "--porcelain"], cwd=submodule_dir())
    return code == 0 and bool(out)


# ── 子命令 ──────────────────────────────────────────────────────────────────
def cmd_status(branch: str) -> int:
    pinned = pinned_commit()
    head = local_head()
    up = upstream_head(branch)
    print(f"submodule : {SUBMODULE_PATH}")
    print(f"上游仓    : {UPSTREAM_REPO}（分支 {branch}）")
    print(f"本仓指针  : {pinned or '(未记录)'}")
    print(f"本地 HEAD : {head or '(未初始化)'}",
          end="")
    if pinned and head:
        print("  [与指针一致]" if pinned == head else "  [与指针不一致]")
    else:
        print()
    print(f"上游 main : {up or '(取不到，需要仓库访问权)'}")
    if up:
        if pinned == up:
            print("状态      : 无漂移")
        elif pinned and pinned != up:
            print("状态      : 上游已前进 —— 跑 `python tools/sync_openqwf.py --update` 更新指针")
        else:
            print("状态      : 指针未记录")
    if local_dirty():
        print("提示      : submodule 工作区有未提交改动（在上游仓里提交，或 checkout 丢弃）")
    return 0


def cmd_init() -> int:
    code, out, err = _git(["submodule", "update", "--init", "--recursive", SUBMODULE_PATH])
    if code != 0:
        print(f"拉取失败：{err or out}", file=sys.stderr)
        return 1
    print(out or "submodule 已就绪")
    return 0


def cmd_update(branch: str) -> int:
    """把指针移到上游最新分支头（不动本仓其它文件）。"""
    if not (submodule_dir() / ".git").exists():
        rc = cmd_init()
        if rc != 0:
            return rc
    code, out, err = _git(
        ["submodule", "update", "--remote", SUBMODULE_PATH], cwd=REPO_ROOT
    )
    if code != 0:
        print(f"更新失败：{err or out}", file=sys.stderr)
        return 1
    print(out or "已更新 submodule 指针")
    print(f"新的本地 HEAD：{local_head()}")
    print("提示：在本仓 `git add vendor/OpenQWF` 后由人决定何时提交。")
    return 0


def cmd_check() -> int:
    """离线自检：指针 == 本地 HEAD，且 submodule 工作区干净。"""
    problems = []
    pinned = pinned_commit()
    head = local_head()
    if not pinned:
        problems.append("本仓没有记录 submodule 指针")
    if head is None:
        problems.append("submodule 未初始化（跑 git submodule update --init --recursive）")
    elif pinned and head != pinned:
        problems.append(f"本地 HEAD {head[:8]} != 本仓指针 {pinned[:8]}")
    if local_dirty():
        problems.append("submodule 工作区有未提交改动")
    if problems:
        for p in problems:
            print(f"[X] {p}")
        return 1
    print(f"[OK] submodule 指针与本地 HEAD 一致（{pinned[:8]}）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="上游 OpenQWF submodule 管理")
    ap.add_argument("--status", action="store_true", help="看指针与上游状态（默认）")
    ap.add_argument("--init", action="store_true", help="首次拉取 submodule")
    ap.add_argument("--update", action="store_true", help="把指针移到上游最新分支")
    ap.add_argument("--check", action="store_true", help="离线自检（指针/本地状态）")
    ap.add_argument("--branch", default=DEFAULT_BRANCH, help=f"上游分支（默认 {DEFAULT_BRANCH}）")
    args = ap.parse_args(argv)

    if args.init:
        return cmd_init()
    if args.update:
        return cmd_update(args.branch)
    if args.check:
        return cmd_check()
    return cmd_status(args.branch)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""漂移检查：本仓的 OpenQWF submodule 指针 vs 上游 `Secsix/OpenQWF`。

模型
----
本仓以 **submodule** 引用上游（`vendor/OpenQWF`），不保存源码副本，所以「漂移」
不再是逐文件比对，而是**指针是否落后**：

* **指针落后**：上游 `main` 已经前进，本仓还钉在旧 commit → 跑
  `python tools/sync_openqwf.py --update` 更新指针。
* **本地状态不一致**：submodule 未初始化 / 本地 HEAD ≠ 本仓指针 / 工作区有未提交改动。

与 `tools/sync_openqwf.py --check` 的区别：那个是**纯离线**自检（只看本仓与本地
submodule）；本脚本额外回答「上游是不是已经前进了」（需要仓库访问权或一个本地 clone）。

用法
----
    python tools/check_openqwf_drift.py                 # 需要仓库访问权
    python tools/check_openqwf_drift.py --from <clone>  # 用本地 clone 比（无网，CI 常用）

退出码：0 = 无漂移；1 = 有漂移；2 = 无法判定（没权限 / 没 clone / 未初始化）。
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


def submodule_dir():
    """submodule 实际目录（兼容历史遗留的小写目录名 ``vendor/openqwf``）。"""
    if SUBMODULE_DIR.exists():
        return SUBMODULE_DIR
    legacy = REPO_ROOT / "vendor" / "openqwf"
    return legacy if legacy.exists() else SUBMODULE_DIR
UPSTREAM_REPO = "https://github.com/Secsix/OpenQWF.git"
DEFAULT_BRANCH = "main"


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
    """本仓记录的 submodule 指针（gitlink → commit）。"""
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


def clone_head(clone_dir: Path) -> Optional[str]:
    """给定本地 clone 的 HEAD。"""
    code, out, _ = _git(["rev-parse", "HEAD"], cwd=clone_dir)
    return out if code == 0 else None


def remote_head(branch: str = DEFAULT_BRANCH) -> Optional[str]:
    """上游 `branch` 的最新 commit（需要仓库访问权）。"""
    code, out, _ = _git(
        ["ls-remote", "--heads", UPSTREAM_REPO, f"refs/heads/{branch}"],
        cwd=REPO_ROOT,
    )
    if code != 0 or not out:
        return None
    return out.split()[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="上游 OpenQWF submodule 漂移检查")
    ap.add_argument("--from", dest="from_clone", type=Path,
                    help="用本地 clone 目录比对上游 HEAD（无网时用）")
    ap.add_argument("--branch", default=DEFAULT_BRANCH,
                    help=f"上游分支（默认 {DEFAULT_BRANCH}）")
    args = ap.parse_args(argv)

    pinned = pinned_commit()
    head = local_head()

    if args.from_clone:
        ref = clone_head(args.from_clone)
        print(f"上游提交参考：{ref or '(取不到)'}")
    else:
        ref = remote_head(args.branch)
        print(f"上游提交参考：{ref or '(取不到，需要仓库访问权)'}")

    if not pinned:
        print("[X] 本仓没有记录 submodule 指针")
        return 1
    if head is None:
        # submodule 没初始化时，本地自检做不了，但「指针 vs 上游」仍然可以判
        print("[!] submodule 未初始化：跳过本地自检（git submodule update --init --recursive）")
    else:
        if head != pinned:
            print(f"[X] 本地 HEAD {head[:8]} != 本仓指针 {pinned[:8]}")
            return 1
        code, out, _ = _git(["status", "--porcelain"], cwd=submodule_dir())
        if code == 0 and out:
            print("[X] submodule 工作区有未提交改动")
            for line in out.splitlines()[:10]:
                print(f"    {line}")
            return 1
    if not ref:
        print("[!] 上游状态未知（无权限/无 clone）——只做了本地自检")
        return 2
    if ref == pinned:
        print(f"[OK] 指针与上游一致（{pinned[:8]}）")
        return 0
    print(f"[X] 上游已前进：上游 {ref[:8]} != 指针 {pinned[:8]}")
    print("-> 更新指针：python tools/sync_openqwf.py --update")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

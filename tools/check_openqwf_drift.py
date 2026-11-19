#!/usr/bin/env python
"""漂移检查：本地 `vendor/openqwf/` 与上游 `Secsix/OpenQWF` 是否还对得上。

与 `tools/sync_openqwf.py --check` 的区别：

* `--check`：**只**比本地 vendor 与 `UPSTREAM.json` 的记录（快、离线、不需要仓库权限）；
  回答的是"本地 vendor 有没有被手工改过"。
* 本脚本：拿**上游真的内容**来比（需要仓库访问权），回答的是
  "本地 vendor 是不是已经落后于上游"。

CI 用它当闸门：上游一有新提交，本仓就被提醒重新同步。

用法
----
    python tools/check_openqwf_drift.py                # 需要仓库访问权
    python tools/check_openqwf_drift.py --from <clone> # 用本地 clone 比（无网）

退出码：0 = 无漂移；1 = 有漂移；2 = 无法判定（没权限 / 没 clone）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor" / "openqwf"
UPSTREAM_META = VENDOR_DIR / "UPSTREAM.json"
UPSTREAM_REPO = "https://github.com/Secsix/OpenQWF.git"
DEFAULT_BRANCH = "main"
VENDORED_PACKAGES = ("qwf", "adapters")
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".pyd")


def _git(args, cwd):
    proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(_normalized_bytes(path.read_bytes(), path.name))
    return h.hexdigest()


TEXT_SUFFIX = (".py", ".json", ".toml", ".md", ".txt", ".cfg", ".ini", ".yml", ".yaml")


def _normalized_bytes(data: bytes, name: str) -> bytes:
    """文本文件统一成 LF 后再比（上游工作区可能是 CRLF，仓库内容是 LF）。"""
    if name.lower().endswith(TEXT_SUFFIX) and b"\r\n" in data:
        return data.replace(b"\r\n", b"\n")
    return data


def _tree_hashes(root: Path) -> dict:
    out = {}
    for pkg in VENDORED_PACKAGES:
        base = root / pkg
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_NAMES for part in path.parts):
                continue
            if path.name.endswith(EXCLUDE_SUFFIX):
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            out[rel] = _sha256(path)
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="检查 vendor/openqwf 与上游的漂移")
    ap.add_argument("--from", dest="from_path", help="用本地 clone 比（不给就尝试 clone 远端）")
    ap.add_argument("--branch", default=DEFAULT_BRANCH, help=f"上游分支（默认 {DEFAULT_BRANCH}）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if not UPSTREAM_META.exists():
        print("[X] 缺少 vendor/openqwf/UPSTREAM.json")
        return 2
    meta = json.loads(UPSTREAM_META.read_text(encoding="utf-8"))
    local_hashes = _tree_hashes(VENDOR_DIR)

    tmp = None
    try:
        if args.from_path:
            source = Path(args.from_path).resolve()
            rc, commit, err = _git(["rev-parse", "HEAD"], source)
            if rc != 0:
                print(f"[X] 不是 git 仓：{source}")
                return 2
        else:
            tmp = Path(tempfile.mkdtemp(prefix="openqwf_drift_"))
            source = tmp / "repo"
            rc, _, err = _git(["clone", "--depth", "1", "--branch", args.branch,
                               UPSTREAM_REPO, str(source)], tmp)
            if rc != 0:
                print(f"[?] 无法访问上游仓（{err.splitlines()[-1] if err else '未知错误'}）："
                      "本次跳过漂移检查")
                return 2
            _, commit, _ = _git(["rev-parse", "HEAD"], source)

        upstream_hashes = _tree_hashes(source)
    finally:
        if tmp and tmp.exists():
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    missing = sorted(set(upstream_hashes) - set(local_hashes))
    extra = sorted(set(local_hashes) - set(upstream_hashes))
    changed = sorted(rel for rel in set(local_hashes) & set(upstream_hashes)
                     if local_hashes[rel] != upstream_hashes[rel])

    pending = bool(meta.get("pending_upstream"))
    result = {
        "upstreamCommit": commit,
        "vendoredCommit": meta.get("commit"),
        "branch": meta.get("branch"),
        "pendingUpstream": pending,
        "missing": missing,
        "extra": extra,
        "changed": changed,
        "drift": bool(missing or extra or changed),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"上游提交：{commit}")
        print(f"已 vendor：{meta.get('commit')}")
        if pending:
            print("  （本地含尚未推给上游的改动 —— 属预期，不是漂移）")
        for rel in missing:
            print(f"[X] 本地缺失：{rel}")
        for rel in extra:
            print(f"[X] 本地多出：{rel}")
        for rel in changed:
            print(f"[X] 内容不同：{rel}")
        if result["drift"]:
            print("-> 需要重新同步：python tools/sync_openqwf.py --from <上游 clone>")
        else:
            print("[OK] vendor 与上游一致")
    return 1 if result["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())

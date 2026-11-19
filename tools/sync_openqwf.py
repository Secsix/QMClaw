#!/usr/bin/env python
"""同步上游 OpenQWF（QWF DSL）到本地 `vendor/openqwf/`。

背景
----
上游 `Secsix/OpenQWF` 是 QWF DSL 的**真源**（本机的 DSL 曾经是从它拆出去的）。
本机不直接改上游源码，而是把它**逐字 vendor** 进
`vendor/openqwf/`（只含 `qwf/` 与 `adapters/` 两个包），然后让
`services/workflow_service/dsl/` 变成一层**兼容门面**（只 re-export，不含语义实现）。

这样两边都保住：

* 上游保持相对独立 —— 上游仍然是真源，改完 push 上游，本机再同步；
* 本机适配 qiskit 后端 —— 本机特有的能力（如多比特 `qubits`）**推给上游**，
  本机只消费，不各自维护一份。

用法
----
    # 从本地 clone 同步（开发时最常用）
    python tools/sync_openqwf.py --from C:\\path\\to\\OpenQWF

    # 从远端拉取后同步（需要仓库访问权）
    python tools/sync_openqwf.py --remote

    # 只校验：vendor/ 与 UPSTREAM.json 记录是否一致（CI 用）
    python tools/sync_openqwf.py --check

    # 查看当前 vendored 版本
    python tools/sync_openqwf.py --status

约定
----
* **只 vendor `qwf/` 与 `adapters/`**：不带 `config/`（真机配置与标定参数绝不进仓）、
  不带 `tools/`（上游自测脚本）、不带 `data/`。
* 同步是**整目录替换**：先清空 `vendor/openqwf/{qwf,adapters}` 再拷，
  避免上游删掉的文件在本地残留（残留文件 = 漂移的温床）。
* 每次同步都写 `vendor/openqwf/UPSTREAM.json`：仓库地址、分支、commit、
  每个文件的 sha256 —— 漂移检查（`tools/check_openqwf_drift.py`）据此比对。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 常量 ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor" / "openqwf"
UPSTREAM_META = VENDOR_DIR / "UPSTREAM.json"

UPSTREAM_REPO = "https://github.com/Secsix/OpenQWF.git"
DEFAULT_BRANCH = "feat/qwf-multiqubit-vocab"

#: 只同步这两个包（目录名 → 包名）
VENDORED_PACKAGES = ("qwf", "adapters")

#: 不进 vendor 的东西
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".pyd")

#: 上游仓里需要一起带过来的非代码资源（相对上游仓根）
EXTRA_FILES = ("pyproject.toml",)

#: 绝不 vendor 的路径片段（真机配置 / 密钥 / 运行数据）
FORBIDDEN_PARTS = ("config", "data", ".env", "secrets", "device_profile.json",
                    "qubitclient.json")


# ── 工具 ────────────────────────────────────────────────────────────────────
#: 参与 vendor 的文件都是文本（.py/.json/.toml/.md）。统一按 **LF** 归一化：
#: 上游仓的 `core.autocrlf=true` 会让它的**工作区**是 CRLF，而 git 仓库里
#: 存的其实是 LF。不归一化的话，两边工作区的字节不同 → 漂移检查全是假警报。
#: 归一化后比对的是**仓库内容**（与上游 commit 里的字节一致）。
TEXT_SUFFIX = (".py", ".json", ".toml", ".md", ".txt", ".cfg", ".ini", ".yml", ".yaml")


def normalize_bytes(data: bytes, name: str) -> bytes:
    """文本文件统一成 LF（CRLF → LF）；二进制原样返回。"""
    if not name.lower().endswith(TEXT_SUFFIX):
        return data
    if b"\r\n" not in data:
        return data
    return data.replace(b"\r\n", b"\n")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(normalize_bytes(path.read_bytes(), path.name))
    return h.hexdigest()


def _iter_files(root: Path) -> List[Path]:
    """列出 root 下所有该 vendor 的文件（相对路径）。"""
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_NAMES]
        for name in filenames:
            if name.endswith(EXCLUDE_SUFFIX):
                continue
            out.append(Path(dirpath) / name)
    return sorted(out)


def _check_safe(rel: str) -> None:
    """拒绝 vendor 真机配置 / 密钥类文件。"""
    parts = rel.replace("\\", "/").split("/")
    if any(part in FORBIDDEN_PARTS for part in parts):
        raise SystemExit(f"拒绝 vendor 敏感路径：{rel}")


def _run_git(args: List[str], cwd: Path) -> str:
    proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def _clone_or_update(url: str, branch: str, dest: Path) -> Tuple[Path, str]:
    """克隆（或更新）上游仓，返回 (仓路径, commit)。"""
    if (dest / ".git").exists():
        _run_git(["fetch", "--depth", "1", "origin", branch], dest)
        _run_git(["checkout", "-B", branch, "FETCH_HEAD"], dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--depth", "1", "--branch", branch, url, str(dest)], dest.parent)
    commit = _run_git(["rev-parse", "HEAD"], dest)
    return dest, commit


def _copy_tree(src: Path, dst: Path) -> List[str]:
    """把 src 目录内容拷到 dst（清空 dst），返回拷贝到的相对路径列表。"""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for path in _iter_files(src):
        rel = path.relative_to(src)
        rel_str = str(rel).replace("\\", "/")
        _check_safe(rel_str)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # 逐字拷贝，但文本文件统一成 LF（见 normalize_bytes 的说明）
        target.write_bytes(normalize_bytes(path.read_bytes(), rel.name))
        copied.append(rel_str)
    return copied


def _write_meta(source: Path, commit: str, branch: str, files: List[str],
                pending_upstream: bool, note: str = "") -> Dict:
    digests: Dict[str, str] = {}
    for rel in files:
        target = VENDOR_DIR / rel
        if target.exists():
            digests[rel] = sha256_of(target)
    meta = {
        "repo": UPSTREAM_REPO,
        "branch": branch,
        "commit": commit,
        "synced_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": str(source),
        "pending_upstream": pending_upstream,
        "vendored_packages": list(VENDORED_PACKAGES),
        "files": digests,
    }
    if note:
        meta["note"] = note
    UPSTREAM_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return meta


def _read_meta() -> Optional[Dict]:
    if not UPSTREAM_META.exists():
        return None
    try:
        return json.loads(UPSTREAM_META.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ── 子命令 ──────────────────────────────────────────────────────────────────
def cmd_sync(args: argparse.Namespace) -> int:
    if args.from_path:
        source = Path(args.from_path).resolve()
        if not (source / "qwf").is_dir():
            raise SystemExit(f"不是上游仓（没有 qwf/ 目录）：{source}")
        branch = args.branch or _run_git(["rev-parse", "--abbrev-ref", "HEAD"], source)
        commit = _run_git(["rev-parse", "HEAD"], source)
        pending = bool(args.mark_pending_upstream)
    else:
        branch = args.branch or DEFAULT_BRANCH
        tmp = Path(tempfile.mkdtemp(prefix="openqwf_sync_"))
        try:
            source, commit = _clone_or_update(UPSTREAM_REPO, branch, tmp / "repo")
        finally:
            pass  # 临时目录交由系统清理

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for pkg in VENDORED_PACKAGES:
        src_pkg = source / pkg
        if not src_pkg.is_dir():
            raise SystemExit(f"上游仓缺少 {pkg}/：{source}")
        copied.extend(f"{pkg}/{rel}" for rel in _copy_tree(src_pkg, VENDOR_DIR / pkg))
    for extra in EXTRA_FILES:
        src_extra = source / extra
        if src_extra.is_file():
            _check_safe(extra)
            (VENDOR_DIR / extra).write_bytes(
                normalize_bytes(src_extra.read_bytes(), extra))
            copied.append(extra)

    meta = _write_meta(source, commit, branch, sorted(copied),
                        pending_upstream=bool(args.mark_pending_upstream),
                        note=args.note or "")
    print(f"已同步 {len(copied)} 个文件 → {VENDOR_DIR}")
    print(f"  分支：{meta['branch']}")
    print(f"  提交：{meta['commit']}")
    if meta["pending_upstream"]:
        print("  ⚠ 标记为 pending_upstream（上游尚未合并此改动）")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """校验 vendor/ 与 UPSTREAM.json 是否一致（文件集合 + 哈希）。"""
    meta = _read_meta()
    if not meta:
        print("[X] 没有 vendor/openqwf/UPSTREAM.json：请先跑一次 sync")
        return 2
    recorded: Dict[str, str] = meta.get("files") or {}
    actual: Dict[str, str] = {}
    for rel in recorded:
        target = VENDOR_DIR / rel
        if target.is_file():
            actual[rel] = sha256_of(target)
    # vendor 里出现但没登记的文件 = 手工塞进去的漂移
    for path in _iter_files(VENDOR_DIR):
        rel = str(path.relative_to(VENDOR_DIR)).replace("\\", "/")
        if rel == UPSTREAM_META.name:
            continue
        if rel not in recorded and rel not in actual:
            actual[rel] = sha256_of(path)
    missing = sorted(set(recorded) - set(actual))
    extra = sorted(set(actual) - set(recorded))
    changed = sorted(rel for rel in set(recorded) & set(actual) if recorded[rel] != actual[rel])
    if not (missing or extra or changed):
        print(f"[OK] vendor 与 UPSTREAM.json 一致（{len(actual)} 个文件，"
              f"commit {meta.get('commit', '?')[:8]}）")
        return 0
    for rel in missing:
        print(f"[X] 缺失：{rel}")
    for rel in extra:
        print(f"[X] 未登记的多余文件：{rel}")
    for rel in changed:
        print(f"[X] 内容被本地改动：{rel}")
    print("-> 本地 vendor 被手工改过或同步不完整；请跑 "
          "`python tools/sync_openqwf.py --from <上游 clone>` 重新同步")
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    meta = _read_meta()
    if not meta:
        print("尚未同步过 vendor/openqwf")
        return 1
    print(json.dumps({k: v for k, v in meta.items() if k != "files"},
                     ensure_ascii=False, indent=2))
    print(f"文件数：{len(meta.get('files') or {})}")
    return 0


def main() -> int:
    # Windows 控制台默认 GBK：把 stdout 换成 UTF-8，避免打不出箭头/对勾
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — 老 Python 没有 reconfigure
        pass
    ap = argparse.ArgumentParser(description="同步 / 校验上游 OpenQWF 到 vendor/openqwf")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--from", dest="from_path", help="从本地 clone 同步")
    group.add_argument("--remote", action="store_true", help="从 GitHub 拉取后同步")
    group.add_argument("--check", action="store_true", help="校验 vendor 是否漂移")
    group.add_argument("--status", action="store_true", help="显示当前 vendored 版本")
    ap.add_argument("--branch", help=f"上游分支（默认 {DEFAULT_BRANCH}）")
    ap.add_argument("--mark-pending-upstream", action="store_true",
                    help="标记 pending_upstream（上游还没合并这个改动）")
    ap.add_argument("--note", default="", help="写入 UPSTREAM.json 的备注")
    args = ap.parse_args()

    if args.check:
        return cmd_check(args)
    if args.status:
        return cmd_status(args)
    return cmd_sync(args)


if __name__ == "__main__":
    sys.exit(main())

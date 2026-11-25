"""仓库卫生自检：防止过程产物 / 个人机器信息 / 大文件 / 私设计文档被提交进仓。

背景（2026-11-25）：本仓的对外口径是「GitHub 上只保留**精简版**——
设计文档、过程文档、本机中间产物一律只留在本地」。光靠 `.gitignore` 挡不住
「已经 `git add` 进去」的情况，所以这里对 **git 已跟踪（tracked）** 的文件集
做一次体检，发现问题就 FAIL。

断言清单：

1. tracked 里没有临时 / 备份 / 日志 / 字节码产物（`*.log` `*.bak` `*.orig`
   `*.rej` `*~` `*.pyc` `__pycache__/` …）。
2. tracked 里没有 > 1 MiB 的大文件（仓里最大的正常文件是 `package-lock.json`
   ~700 KB，所以 1 MiB 是安全线）。
3. `docs/` 下没有私设计 / 计划 / 进度类文档（DESIGN / PLAN / ROADMAP / TODO /
   BACKLOG / REVIEW / PROOFREAD / UPSTREAM_ …）。
4. 关键目录都有 `README.md`（新来的人能看懂每个目录是干什么的）。
5. 仓库根只允许白名单里的 `.py`（防止根目录再次长出一堆一次性脚本）。
6. `.gitignore` 里的关键规则还在（有人手改 .gitignore 时能立刻发现）。
7. tracked 文件里没有个人机器的绝对路径（本机用户名 / 本机仓根 / 旧仓根）。

本自检是纯静态（只读 git 索引 + 读文件），可进 `run_all_checks.py` OFFLINE。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:  # 控制台 GBK 时不要让 print 把自检打崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

PASS = 0
FAIL = 0
SKIP = 0

# ---------------------------------------------------------------------------
# 1) 临时 / 备份 / 日志 / 字节码产物
# ---------------------------------------------------------------------------
JUNK_SUFFIXES = (
    ".log", ".bak", ".orig", ".rej", ".swp", ".swo", ".tmp", ".temp",
    ".pyc", ".pyo", ".pid", ".DS_Store", ".coverage",
)
JUNK_NAME_SUFFIXES = ("~",)
JUNK_PATH_PARTS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")

# ---------------------------------------------------------------------------
# 2) 大文件
# ---------------------------------------------------------------------------
MAX_TRACKED_BYTES = 1024 * 1024  # 1 MiB

# ---------------------------------------------------------------------------
# 3) docs/ 下的私文档（对外只放使用面 / 运维面）
# ---------------------------------------------------------------------------
PRIVATE_DOC_PATTERNS = (
    "*DESIGN*.md", "*PLAN*.md", "*ROADMAP*.md", "TODO_*.md", "BACKLOG.md",
    "*_REVIEW.md", "*_PROOFREAD.md", "UPSTREAM_*.md", "GIT_HISTORY.md",
    "CHANGES_VS_SOURCE.md", "SYNC_*_MERGE_PLAN.md",
)

# ---------------------------------------------------------------------------
# 4) 必须有 README 的目录
# ---------------------------------------------------------------------------
DIRS_WITH_README = (
    "docs", "web", "skills", "qwf_examples", "server", "server/services",
    "tools", "checks", "qwf_scripts", "experiments",
)

# ---------------------------------------------------------------------------
# 5) 仓库根允许存在的 .py（其余根级 .py 都是本机一次性脚本）
# ---------------------------------------------------------------------------
ROOT_PY_ALLOWLIST = {"qwf_gen.py", "qwf_lint.py", "qwf_run.py", "reg_snapshot.py"}

# ---------------------------------------------------------------------------
# 6) .gitignore 必须保留的规则（有人手改时能立刻发现）
# ---------------------------------------------------------------------------
REQUIRED_IGNORE_RULES = (
    "server/config/memory/",
    "server/config/device_profile.json",
    "/server/config/*.local.json",
    "/docs/*DESIGN*.md",
    "/docs/*PLAN*.md",
    "/docs/TODO_*.md",
    "/docs/BACKLOG.md",
    "/experiments/reports/",
    "/be2_work/",
    ".Codex/",
)

# ---------------------------------------------------------------------------
# 7) 个人机器绝对路径
# ---------------------------------------------------------------------------
# 命中的都是「这台开发机的私路径」，一律不允许出现在 tracked 文件里。
PERSONAL_PATTERNS = (
    r"C:\\Users\\lqcs",
    r"D:\\Documents\\QMClaw",
    r"d:\\Documents\\QMClaw",
    r"D:\\QMClaw",
)
# 命中即放行的整文件（检查脚本自己会把这些路径当「已知历史样例」写进去）
PERSONAL_FILE_ALLOWLIST = {
    "checks/_docs_paths_check.py",
}
# 允许出现的机器路径前缀（实验室机 / 通用占位 / 工具默认）
ALLOWED_PATH_HINTS = (
    "D:\\qmclaw",            # 实验室机部署根（小写 q），团队约定
    "D:\\DataVault",         # 真机数据源
    "D:\\semester4",         # 实验室机环境
    "D:\\anaconda3",
    "D:\\out",
    "D:\\path\\to",          # 文档占位
    "C:\\Users\\你的用户名",
    "C:\\Users\\your_name",
    "C:\\Users\\39045",
    "C:\\Program Files",
)

TEXT_SUFFIXES = {
    ".md", ".py", ".ps1", ".sh", ".bat", ".cmd", ".ts", ".tsx", ".js", ".jsx",
    ".json", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".qwf", ".example",
    ".gitignore", ".gitattributes", ".gitmodules",
}


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [skip] {name} —— {why}")


def tracked_files() -> list[str]:
    """git 已跟踪的文件清单（相对仓库根，POSIX 风格）。"""
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(ROOT), capture_output=True,
    )
    if proc.returncode != 0:
        return []
    raw = proc.stdout.decode("utf-8", errors="replace")
    return [p for p in raw.split("\0") if p]


def read_text(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    except Exception:
        return ""


def main() -> int:
    files = tracked_files()
    if not files:
        print("  [FAIL] 读不到 tracked 文件清单（git ls-files 失败）")
        print(f"\n_repo_hygiene_check: PASS={PASS} FAIL={FAIL + 1} SKIP={SKIP}")
        return 1

    print(f"[1] 临时 / 备份 / 日志产物（tracked 共 {len(files)} 个文件）")
    junk = []
    for rel in files:
        p = Path(rel)
        name = p.name
        if name.endswith(JUNK_NAME_SUFFIXES) or name.endswith(JUNK_SUFFIXES):
            junk.append(rel)
            continue
        if any(part in JUNK_PATH_PARTS for part in p.parts):
            junk.append(rel)
    check("tracked 里没有临时/备份/日志/字节码产物", not junk,
          f"{len(junk)} 个：" + ", ".join(junk[:10]))

    print("[2] 大文件")
    big = []
    for rel in files:
        p = ROOT / rel
        try:
            if p.is_file() and p.stat().st_size > MAX_TRACKED_BYTES:
                big.append(f"{rel} ({p.stat().st_size // 1024} KB)")
        except OSError:
            continue
    check(f"tracked 里没有 > {MAX_TRACKED_BYTES // 1024 // 1024} MiB 的文件", not big,
          f"{len(big)} 个：" + ", ".join(big[:5]))

    print("[3] docs/ 只放对外文档")
    private = []
    for rel in files:
        if not rel.startswith("docs/"):
            continue
        name = Path(rel).name
        if any(Path(name).match(pat) for pat in PRIVATE_DOC_PATTERNS):
            private.append(rel)
    check("docs/ 下没有私设计/计划/进度类文档", not private,
          ", ".join(private[:10]))

    print("[4] 关键目录有 README")
    missing = []
    for d in DIRS_WITH_README:
        if not (ROOT / d / "README.md").is_file():
            missing.append(d)
    check("关键目录都有 README.md", not missing, "缺：" + ", ".join(missing))

    print("[5] 仓库根脚本白名单")
    root_py = {Path(r).name for r in files
               if "/" not in r and r.endswith(".py")}
    stray = sorted(root_py - ROOT_PY_ALLOWLIST)
    check("仓库根只保留白名单 .py", not stray, "多出：" + ", ".join(stray))

    print("[6] .gitignore 关键规则")
    gi = read_text(ROOT / ".gitignore")
    lost = [rule for rule in REQUIRED_IGNORE_RULES if rule not in gi]
    check(".gitignore 关键规则齐全", not lost, "缺：" + ", ".join(lost))

    print("[7] 个人机器绝对路径")
    hits = []
    pat = re.compile("|".join(PERSONAL_PATTERNS))
    for rel in files:
        if rel in PERSONAL_FILE_ALLOWLIST:
            continue
        p = ROOT / rel
        if p.suffix.lower() not in TEXT_SUFFIXES and p.name not in TEXT_SUFFIXES:
            continue
        if not p.is_file():
            continue
        text = read_text(p)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not pat.search(line):
                continue
            if any(h in line for h in ALLOWED_PATH_HINTS):
                continue
            hits.append(f"{rel}:{i}: {line.strip()[:90]}")
    check("tracked 文件里没有个人机器绝对路径", not hits,
          f"{len(hits)} 处：" + "; ".join(hits[:5]))

    print(f"\n_repo_hygiene_check: PASS={PASS} FAIL={FAIL} SKIP={SKIP}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

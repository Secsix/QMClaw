"""仓库边界自检：公共目录里不许漏出「真机后端」的实现细节。

背景（2026-11-27）：本仓分层的硬规矩是「**所有调用底层测控软件的行为都收进测控微服务**」。
公共目录（`checks/` `docs/` `experiments/` `memory/` `qwf_scripts/` `tools/`）
里的内容会被别人读、被 Agent 读、被 CI 读；一旦里面出现厂商库的调用写法或真机比特编号，
读者就会绕过服务直接动手——绕过的是写闸门、审计、串行锁与后端适配层。

断言清单（纯静态，读磁盘上的公共目录文本文件）：

A. 全部公共目录：
   1. 没有厂商库的 `import` / `from ... import`（`lqms` / `qter` / `pylabrad` / `labrad`）。
   2. 没有真机标识（比特编号 / 会话编号 / 机房代号 / 真机环境目录）。
B. 可执行文件（`tools/` `qwf_scripts/` `experiments/` 下的 .py/.qwf/.cmd/.sh）：
   3. 没有厂商调用片段（`sq.<fn>(...)`）。
   4. 没有把厂商调用当命令写（`qter.fitData(...)`、`QubitUpdater(...)`、`labrad_connect`）。
   5. 没有自己打开厂商数据文件（`import h5py` / `h5py.File`）。

豁免：
- 本自检文件自身（规则写在这里必然命中）。
- `docs/SERVICE_BOUNDARY.md` / `docs/REAL_MACHINE.md` / `docs/ADAPTER_GUIDE.md`
  —— 这三份文档的职责就是把边界写清楚，红线词出现在它们里面是应该的。
- `docs/` 里的 `sq.<fn>(...)` 示例：`/execute` 这类端点的入参就是一段代码字符串，
  运维文档必须写清它的样子；但**代码**里不许出现这种片段。
- 文档字符串与引号里的样例片段（自检脚本里常拿它们当断言材料）。
- 政策句：写明「不要直连 / 由服务处理 / 红线 / 已下沉」的行允许出现这些词。
  禁止的是**可执行的做法**，不是把红线写清楚。

用法：
    python checks/_repo_boundary_check.py
退出码：0 全过；1 有违例。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:  # 控制台 GBK 时不要让自检打崩
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

# 公共目录：入库后会被别人读到的那几处
SCAN_DIRS = (
    "checks",
    "docs",
    "experiments",
    "memory",
    "qwf_scripts",
    "tools",
)

# B 组规则只管「会被执行的文件」
CODE_DIRS = ("experiments", "qwf_scripts", "tools")
CODE_SUFFIXES = {".py", ".qwf", ".cmd", ".sh", ".ps1"}

# 只看文本文件；二进制与依赖目录不看
TEXT_SUFFIXES = {".md", ".py", ".qwf", ".json", ".txt", ".yml", ".yaml", ".sh", ".cmd", ".ps1"}

SKIP_DIR_PARTS = {
    "__pycache__",
    "node_modules",
    ".git",
    "vendor",
    "reports",
    "s21_image",
}

# 豁免文件（相对仓库根）
EXEMPT_FILES = {
    "checks/_repo_boundary_check.py",
    "docs/SERVICE_BOUNDARY.md",
    "docs/REAL_MACHINE.md",
    "docs/ADAPTER_GUIDE.md",
}

PASS = 0
FAIL = 0

# A 组：全部公共目录都查
RULES_ALL = (
    (
        "厂商库 import",
        re.compile(r"(?m)^\s*(?:from|import)\s+(?:lqms|qter|pylabrad|labrad)\b"),
    ),
    (
        "真机标识",
        re.compile(
            r"(?<![\w])q\d+ld\d+(?![\w])"
            r"|(?<![\w])20\d{6}(?:_[A-Za-z0-9]+)+(?![\w])"
            r"|(?<![\w])LQHL(?![\w])"
            r"|[\\/]envs[\\/]lqcs\b"
        ),
    ),
)

# B 组：只查会被执行的文件
RULES_CODE = (
    (
        "厂商调用片段",
        re.compile(r"(?<![\w.])sq\s*\.\s*[A-Za-z_]\w*\s*\("),
    ),
    (
        "厂商调用当命令写",
        re.compile(r"qter\s*\.\s*fitData|QubitUpdater\s*\(|\blabrad_connect\b|labrad\s*\.\s*connect"),
    ),
    (
        "自开厂商数据文件",
        re.compile(r"(?m)^\s*(?:import|from)\s+h5py\b|\bh5py\s*\.\s*File\b"),
    ),
)

# 政策句：允许在这些句子里出现厂商词（写清楚红线本身是好事）
POLICY_HINTS = (
    "不要直连",
    "不要在沙箱",
    "铁律",
    "红线",
    "由服务",
    "服务里",
    "服务侧",
    "微服务",
    "收进",
    "已收进",
    "下沉",
    "禁止",
    "不允许",
    "不许",
    "只能",
    "边界",
    "豁免",
    "端点",
)


def _is_policy_line(line: str) -> bool:
    return any(hint in line for hint in POLICY_HINTS)


def _optional_import(lines: list[str], lineno: int) -> bool:
    """该 import 是否被 `except ImportError` 兜底（可选依赖写法）。

    自检脚本为了在缺厂商包的机器上也能跑，会这样写：
        try:
            import lqms.pyle as lqms_pyle
        except ImportError as e:
            ...
    这种写法不构成「绕过微服务直连厂商库」——它只是让自检在缺包时记 SKIP。
    """
    tail = lines[lineno : lineno + 4]
    return any("except ImportError" in line for line in tail)


def _match_inside_string(line: str, start: int) -> bool:
    """判断匹配点是否落在这一行的引号字符串里（自检脚本常拿它当断言材料）。"""
    head = line[:start]
    if head.count("'") % 2 == 1:
        return True
    if head.count('"') % 2 == 1:
        return True
    return False


def _iter_files():
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel_parts = set(path.relative_to(ROOT).parts)
            if rel_parts & SKIP_DIR_PARTS:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel in EXEMPT_FILES:
                continue
            yield path


def check_text(path: Path) -> int:
    """返回该文件的违例条数。"""
    global PASS, FAIL
    rel = path.relative_to(ROOT).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {rel}: 读不出来（{exc}）")
        FAIL += 1
        return 1

    rules = list(RULES_ALL)
    if rel.split("/")[0] in CODE_DIRS and path.suffix.lower() in CODE_SUFFIXES:
        rules += list(RULES_CODE)

    problems = 0
    in_docstring = False
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        quotes = line.count('"""') + line.count("'''")
        if in_docstring:
            if quotes % 2 == 1:
                in_docstring = False
            continue
        if not line.strip() or _is_policy_line(line):
            if quotes % 2 == 1:
                in_docstring = True
            continue
        for label, pattern in rules:
            match = pattern.search(line)
            if not match:
                continue
            if _match_inside_string(line, match.start()):
                continue
            if label == "厂商库 import" and _optional_import(lines, lineno):
                # 自检脚本常以「可选依赖」方式 import 厂商包（try/except ImportError 兜底，
                # 缺包的机器记 SKIP）。这种形式允许；直连用法不允许。
                continue
            problems += 1
            snippet = line.strip()
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            print(f"[FAIL] {rel}:{lineno} [{label}] {match.group(0)!r} <- {snippet}")
        if quotes % 2 == 1:
            in_docstring = True

    if problems == 0:
        PASS += 1
        print(f"[PASS] {rel}")
    else:
        FAIL += 1
    return problems


def main() -> int:
    files = list(_iter_files())
    if not files:
        print("[FAIL] 公共目录里没扫到任何文本文件")
        return 1

    total = 0
    for path in files:
        total += check_text(path)

    print()
    print(f"文件 {len(files)} 个 · PASS={PASS} FAIL={FAIL} · 违例 {total} 条")
    if total:
        print(
            "修法：把可执行做法改写成「调哪个语义端点」，"
            "厂商实现放回 server/services/ 对应的微服务里；"
            "并由 docs/SERVICE_BOUNDARY.md 记录该能力落在哪个微服务。"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

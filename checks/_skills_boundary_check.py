"""技能边界自检：`skills/` 里不许漏底层测控软件的细节。

背景（2026-11-26）：本仓的分层约定是「**所有调用底层测控软件的行为都收进测控微服务**，
技能只记怎么判断、按什么顺序、门限是多少」。厂商库名、数据格式、本机路径一旦写进技能，
Agent 就会绕过服务直接动手——绕过的是写闸门、审计、串行锁与后端的适配层。

断言清单（纯静态，读磁盘上的 `skills/**/*.md`）：

1. 没有厂商库的 `import` / `from ... import`（`lqms` / `qter` / `pylabrad` 等）。
2. 没有把厂商调用当命令写（`qter.fitData(...)`、`QubitUpdater`）。
3. 没有数据格式细节（`hdf5` / `.pyd`）——技能不需要知道数据长什么样。
4. 没有本机绝对路径（`D:\\` / `C:\\` / `/d/`）——技能要能换机器跑。
5. 没有直连设备的客户端写法（`labrad_client`、`import pylabrad`）。

豁免：写明「由服务处理 / 不要直连 / 铁律 / 不要在沙箱进程里」的政策句允许出现这些词
——禁止的是**可执行的做法**，不是把红线写清楚。

用法：
    python checks/_skills_boundary_check.py
退出码：0 全过；1 有违例。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:  # 控制台 GBK 时不要让自检打崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

PASS = 0
FAIL = 0

# 需要扫描的文本（技能正文 + 给人看的说明）
GLOBS = ("**/SKILL.md", "**/README.md")

# (说明, 正则)
RULES = (
    (
        "厂商库 import",
        re.compile(r"(?m)^\s*(?:from|import)\s+(?:lqms|qter|pylabrad|labrad)\b"),
    ),
    (
        "厂商调用当命令写",
        re.compile(r"qter\s*\.\s*fitData|QubitUpdater\s*\("),
    ),
    (
        "数据格式细节",
        re.compile(r"\bhdf5\b|\.pyd\b", re.IGNORECASE),
    ),
    (
        "本机绝对路径",
        re.compile(r"\b[A-Za-z]:\\\\|\b[A-Za-z]:\\|/d/[A-Za-z]"),
    ),
    (
        "直连设备客户端",
        re.compile(r"labrad_client|from\s+pylabrad"),
    ),
)

# 政策句：允许在这些句子里出现厂商词（写清楚红线本身是好事）
POLICY_HINTS = (
    "不要直连",
    "不要在沙箱进程里",
    "铁律",
    "由服务",
    "服务里",
    "服务侧",
    "不读文件",
    "不解析",
    "不进技能",
    "微服务",
    "收进",
    "已收进",
    "不 import",
    "不写",
    "禁止",
    "不允许",
    "不下发",
)


def _is_policy_line(line: str) -> bool:
    return any(hint in line for hint in POLICY_HINTS)


def _iter_files():
    seen = []
    for pattern in GLOBS:
        for path in sorted(SKILLS.glob(pattern)):
            if path not in seen:
                seen.append(path)
    return seen


def check_text(path: Path) -> int:
    """返回该文件的违例条数。"""
    global PASS, FAIL
    rel = path.relative_to(ROOT)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {rel}: 读不出来（{exc}）")
        FAIL += 1
        return 1

    problems = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or _is_policy_line(line):
            continue
        for label, pattern in RULES:
            match = pattern.search(line)
            if not match:
                continue
            problems += 1
            snippet = line.strip()
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            print(f"[FAIL] {rel}:{lineno} [{label}] {match.group(0)!r} <- {snippet}")

    if problems == 0:
        PASS += 1
        print(f"[PASS] {rel}")
    else:
        FAIL += 1
    return problems


def main() -> int:
    if not SKILLS.is_dir():
        print(f"[FAIL] 找不到技能目录：{SKILLS}")
        return 1

    files = _iter_files()
    if not files:
        print(f"[FAIL] {SKILLS} 下没有任何 SKILL.md / README.md")
        return 1

    total = 0
    for path in files:
        total += check_text(path)

    print()
    print(f"文件 {len(files)} 个 · PASS={PASS} FAIL={FAIL} · 违例 {total} 条")
    if total:
        print(
            "修法：把可执行做法改写成「调哪个语义端点」，"
            "并由 docs/SERVICE_BOUNDARY.md 记录该能力落在哪个微服务。"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

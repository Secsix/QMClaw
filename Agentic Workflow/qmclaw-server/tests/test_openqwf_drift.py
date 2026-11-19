"""上游 OpenQWF vendor 副本的门面 / 漂移自检。

三类断言：

1. **门面纯度** —— `services/workflow_service/dsl/` 下不再有 DSL 语义实现：
   每个模块文件都只是"指向上游"的薄壳（`sys.modules[__name__] = upstream(...)`）。
   这一条是"上游是唯一真源"的**结构性**保证：有人在本仓偷偷加一份实现，
   这里就会红。
2. **同一性** —— 门面模块与上游模块是**同一个对象**（不是拷贝）：
   `dsl.const is qwf.const`、`dsl.textdsl.parser is qwf.textdsl.parser`、
   `DSLSyntaxError` 只有一个类。
3. **双表不漂移** —— 运行期的 `services/common/replay.py` 与上游
   `qwf/vendor_semantics.py` 两份厂商语义表必须逐字段一致。

真正的"上游漂移"（vendor 副本 vs 上游仓）需要能访问上游仓，由
`tools/check_openqwf_drift.py` + CI 的 `openqwf-drift` 任务负责；
这里在拿不到上游时**跳过**，不阻塞离线 CI。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent.parent
VENDOR_DIR = REPO_ROOT / "vendor" / "openqwf"
DSL_DIR = SERVER_ROOT / "services" / "workflow_service" / "dsl"

#: 门面里**允许**有实质逻辑的文件（路径 → 原因）
EXEMPT = {
    "_upstream.py": "桥接层：找 vendor 目录 + 注入路径环境变量",
    "__init__.py": "门面入口：跑桥接 + 再导出公开 API",
    "validate.py": "上游把 qubit_arity 收成私有名，这里补回公开名",
    "llm.py": "上游 server_root() 指向上游仓根，这里覆写成本地仓根",
    "vendor_semantics.py": "厂商语义表的惰性代理（上游是唯一真源）",
    "backends/__init__.py": "后端注册表再导出（上游在独立顶层包 adapters/）",
    "textdsl/__init__.py": "子模块提前登记，避免模块对象分裂",
}


def _py_files() -> list[pathlib.Path]:
    return sorted(p for p in DSL_DIR.rglob("*.py"))


def test_vendor_copy_present():
    """vendor 副本必须在（没有它门面就没法工作）。"""
    assert (VENDOR_DIR / "qwf" / "__init__.py").exists(), (
        f"vendor 副本缺失：{VENDOR_DIR} —— 先跑 python tools/sync_openqwf.py"
    )
    assert (VENDOR_DIR / "adapters" / "__init__.py").exists(), "vendor 副本缺 adapters/"


def test_facade_has_no_dsl_implementation():
    """门面里不能有 DSL 语义实现：每个非豁免文件都必须是"指向上游"的薄壳。"""
    offenders = []
    for path in _py_files():
        rel = path.relative_to(DSL_DIR).as_posix()
        if rel in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        if "sys.modules[__name__] = upstream(" not in text:
            offenders.append(rel)
            continue
        # 薄壳不该超过 20 行有效代码（多一行都不行：实现不许回流到门面）
        body = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if len(body) > 20:
            offenders.append(f"{rel}（{len(body)} 行，超出薄壳上限）")
    assert not offenders, "门面里出现了实体实现：" + "; ".join(offenders)


def test_facade_modules_are_upstream_objects():
    """门面模块 == 上游模块（同一对象，不是拷贝）。"""
    import services.workflow_service.dsl as dsl  # 先导入：它负责把 vendor 目录接上
    import adapters.lqcs  # noqa: F401
    import qwf.const
    import qwf.textdsl.parser
    import services.workflow_service.dsl.const as dconst
    import services.workflow_service.dsl.textdsl.parser as dparser

    assert dconst is qwf.const
    assert dparser is qwf.textdsl.parser
    assert dsl.DSLSyntaxError is qwf.textdsl.parser.DSLSyntaxError


def test_backends_registry_lists_lqcs_and_qiskit():
    """适配器注册表里有真机与 qiskit 两条路（qiskit 是转发，不是复刻）。"""
    from services.workflow_service.dsl import list_backends

    backends = list_backends()
    assert "lqcs" in backends
    assert "qiskit" in backends


def test_vendor_semantics_tables_match():
    """运行期的厂商语义表与上游那份必须一致（两张表不许各说各话）。"""
    from services.common.replay import VENDOR_SEMANTICS as local_table
    from services.workflow_service.dsl.vendor_semantics import VENDOR_SEMANTICS as upstream_table

    assert set(local_table) == set(upstream_table), (
        "厂商语义表的键不一致："
        f"仅本地有 {sorted(set(local_table) - set(upstream_table))}，"
        f"仅上游有 {sorted(set(upstream_table) - set(local_table))}"
    )
    for fn in sorted(local_table):
        assert local_table[fn] == upstream_table[fn], f"厂商语义表不一致：{fn}"


def test_vendor_files_use_lf_endings():
    """vendor 副本统一 LF（CRLF 会让 sha256 比对出假漂移）。"""
    bad = []
    for path in sorted(VENDOR_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".toml", ".json", ".md"):
            continue
        raw = path.read_bytes()
        if b"\r\n" in raw:
            bad.append(path.relative_to(REPO_ROOT).as_posix())
    assert not bad, "vendor 副本里出现 CRLF：" + ", ".join(bad[:10])


@pytest.mark.skipif(
    not os.environ.get("OPENQWF_UPSTREAM_DIR"),
    reason="需要上游工作副本（设 OPENQWF_UPSTREAM_DIR 才跑）",
)
def test_vendor_matches_upstream_tree():
    """vendor 副本 == 上游工作副本（逐文件 sha256，行尾归一）。"""
    upstream = pathlib.Path(os.environ["OPENQWF_UPSTREAM_DIR"]).resolve()
    if not (upstream / "qwf" / "__init__.py").exists():
        pytest.skip(f"{upstream} 不是上游仓副本")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_openqwf_drift.py"),
         "--from", str(upstream)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr

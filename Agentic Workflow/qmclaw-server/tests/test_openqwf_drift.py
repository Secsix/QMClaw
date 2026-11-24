"""上游 OpenQWF（submodule）的门面 / 自检。

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

**上游怎么来**：本仓以 **submodule**（`vendor/OpenQWF` → `Secsix/OpenQWF`）引用上游，
本仓**不保存源码副本**。所以"副本 vs 上游"的逐文件比对没有了，取而代之的是
"**指针是否落后**"——由 `tools/check_openqwf_drift.py` + CI 的 `openqwf-drift`
任务负责；这里只做本仓自洽性检查（指针 == 本地 HEAD、工作区干净）。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent.parent
SUBMODULE_DIR = REPO_ROOT / "vendor" / "openqwf"
DSL_DIR = SERVER_ROOT / "services" / "workflow_service" / "dsl"

#: 门面里**允许**有实质逻辑的文件（路径 → 原因）
EXEMPT = {
    "_upstream.py": "桥接层：找 submodule 目录 + 注入路径环境变量",
    "__init__.py": "门面入口：跑桥接 + 再导出公开 API",
    "validate.py": "上游把 qubit_arity 收成私有名，这里补回公开名",
    "llm.py": "上游 server_root() 指向上游仓根，这里覆写成本地仓根",
    "vendor_semantics.py": "厂商语义表的惰性代理（上游是唯一真源）",
    "backends/__init__.py": "后端注册表再导出（上游在独立顶层包 adapters/）",
    "textdsl/__init__.py": "子模块提前登记，避免模块对象分裂",
}


def _py_files() -> list[pathlib.Path]:
    return sorted(p for p in DSL_DIR.rglob("*.py"))


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    return proc.stdout.strip()


def test_submodule_present():
    """上游 submodule 必须就位（没有它门面就没法工作）。"""
    if not (SUBMODULE_DIR / ".git").exists():
        pytest.skip("submodule 未初始化（跑 git submodule update --init --recursive）")
    assert (SUBMODULE_DIR / "qwf" / "__init__.py").exists(), (
        f"上游 submodule 不完整：{SUBMODULE_DIR}"
    )
    assert (SUBMODULE_DIR / "adapters" / "__init__.py").exists(), "上游 submodule 缺 adapters/"


def test_pinned_commit_matches_local_head():
    """本仓记录的指针（gitlink）== 本地 submodule HEAD，且工作区干净。"""
    if not (SUBMODULE_DIR / ".git").exists():
        pytest.skip("submodule 未初始化")
    ls_tree = _git("ls-tree", "HEAD", "--", "vendor/OpenQWF")
    assert ls_tree, "本仓 HEAD 里没有 vendor/OpenQWF 的 gitlink"
    mode, kind, sha = ls_tree.split()[0], ls_tree.split()[1], ls_tree.split()[2]
    assert mode == "160000", f"vendor/OpenQWF 必须是 submodule（160000），实际 {mode} {kind}"
    local = _git("-C", str(SUBMODULE_DIR), "rev-parse", "HEAD")
    assert local == sha, f"本地 HEAD {local[:8]} != 本仓指针 {sha[:8]}"
    status = _git("-C", str(SUBMODULE_DIR), "status", "--porcelain")
    assert not status, "submodule 工作区有未提交改动：" + "; ".join(status.splitlines()[:10])


def test_no_source_copy_in_index():
    """本仓**不得**再跟踪上游源码文件（只能有一个 gitlink）。"""
    tracked = _git("ls-files", "--", "vendor/OpenQWF")
    assert tracked in ("", "vendor/OpenQWF"), (
        "本仓里出现了上游源码副本条目：\n" + tracked[:500]
    )


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
    import services.workflow_service.dsl as dsl  # 先导入：它负责把 submodule 接上
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


@pytest.mark.skipif(
    not os.environ.get("OPENQWF_UPSTREAM_DIR"),
    reason="需要上游工作副本（设 OPENQWF_UPSTREAM_DIR 才跑）",
)
def test_pointer_matches_upstream_tree():
    """本仓指针 == 上游工作副本的 HEAD（拿不到上游就跳过，不阻塞离线 CI）。"""
    upstream = pathlib.Path(os.environ["OPENQWF_UPSTREAM_DIR"]).resolve()
    if not (upstream / "qwf" / "__init__.py").exists():
        pytest.skip(f"{upstream} 不是上游仓副本")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_openqwf_drift.py"),
         "--from", str(upstream)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr

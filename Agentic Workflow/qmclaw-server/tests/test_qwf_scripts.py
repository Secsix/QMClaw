"""`qwf_scripts/` 实测脚本的自检。

守住三件事：

1. **脚本集合 = 实验词表** —— `qwf_scripts/*.qwf` 和
   `config/experiment_configs.json` 的 `experiments` 一一对应。
   少一个 = 那个实验没法用命令跑；多一个 = 词表里没有的野实验。
2. **每个脚本 lint 干净** —— `error=0`（warning 允许：那些是"这个实验会回写
   寄存器"之类有信息量的提示，不是脚本写错了）。
3. **比特元数对得上** —— 词表里 `qubits: 2` 的实验，脚本必须写 `qubits [...]`
   而不是 `qubit`（元数错了校验阶段就拦，E_QUBIT_ARITY）。

不联网、不起服务、不碰硬件（纯静态）。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent.parent
SCRIPTS_DIR = REPO_ROOT / "qwf_scripts"
CONFIG_PATH = SERVER_ROOT / "config" / "experiment_configs.json"

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def _experiments() -> dict:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return data.get("experiments") or {}


def _script_files() -> list[pathlib.Path]:
    if not SCRIPTS_DIR.is_dir():
        return []
    return sorted(SCRIPTS_DIR.glob("*.qwf"))


def test_scripts_dir_exists():
    assert SCRIPTS_DIR.is_dir(), f"缺少实测脚本目录：{SCRIPTS_DIR}"
    assert _script_files(), "qwf_scripts/ 里一个 .qwf 都没有"


def test_script_set_matches_experiment_table():
    """脚本名 = 词表里的实验键（词表有 13 个，脚本就得有 13 份）。"""
    expected = set(_experiments())
    actual = {p.stem for p in _script_files()}
    assert actual == expected, (
        f"脚本集合与实验词表不一致："
        f"词表多出 {sorted(expected - actual)}；"
        f"脚本多出 {sorted(actual - expected)}"
    )


@pytest.mark.parametrize("path", _script_files(), ids=lambda p: p.stem)
def test_script_lints_without_errors(path):
    from services.workflow_service.dsl.lint import lint_text

    report = lint_text(path.read_text(encoding="utf-8"))
    errors = report.get("errors") or []
    assert not errors, (
        f"{path.name} 有 lint error："
        + "; ".join(f"{e.get('code')} {e.get('message')}" for e in errors[:5])
    )


@pytest.mark.parametrize("path", _script_files(), ids=lambda p: p.stem)
def test_script_qubit_arity_matches_table(path):
    """双比特实验必须写 `qubits [...]`，单比特必须写 `qubit`。"""
    from services.workflow_service.dsl.lint import lint_text

    table = _experiments().get(path.stem)
    assert table is not None, f"词表里没有 {path.stem}"

    text = path.read_text(encoding="utf-8")
    report = lint_text(text)
    summary = report.get("summary") or {}
    nq = int(table.get("qubits") or 1)

    has_qubits_list = "qubits " in text or "qubits  " in text
    has_single = "qubit   $" in text or "qubit $" in text

    if nq == 2:
        assert has_qubits_list, f"{path.name} 是双比特实验，必须写 `qubits [\"q0\", \"q1\"]`"
        assert not has_single, f"{path.name} 同时写了 qubit 和 qubits（DSL 禁止）"
    else:
        assert has_single, f"{path.name} 是单比特实验，应该写 `qubit $env.qubit`"
        assert not has_qubits_list, f"{path.name} 是单比特实验，不该写 qubits 列表"

    # lint 摘要里的 qubit 数也要等于词表声明
    if summary.get("qubits"):
        assert len(summary["qubits"]) == nq, (
            f"{path.name} 实际用了 {summary['qubits']}，词表声明 {nq} 比特"
        )

"""`qwf_scripts/` 实测脚本的自检。

脚本分两层，规则不同：

* `qwf_scripts/experiments/` —— **单项脚本**（一份一个实验）。
  守住三件事：
  1. **脚本集合 = 实验词表** —— 和 `config/experiment_configs.json` 的
     `experiments` 一一对应。少一个 = 那个实验没法用命令跑；
     多一个 = 词表里没有的野实验。
  2. **每个脚本 lint 干净** —— `error=0`（warning 允许：那些是"这个实验会回写
     寄存器"之类有信息量的提示，不是脚本写错了）。
  3. **比特元数对得上** —— 词表里 `qubits: 2` 的实验，脚本必须写 `qubits [...]`
     而不是 `qubit`（元数错了校验阶段就拦，E_QUBIT_ARITY）。
* `qwf_scripts/workflows/` —— **工作流**（一条流程多个实验）。
  守住：lint 干净 + 每条流程至少 2 个实验（一条流程只干一件事就不算工作流了）
  + 名字不和单项脚本重名（`tools/qwf_exp.py` 按名字选脚本，重名会歧义）。

不联网、不起服务、不碰硬件（纯静态）。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent.parent
SCRIPTS_DIR = REPO_ROOT / "qwf_scripts"
EXPERIMENTS_DIR = SCRIPTS_DIR / "experiments"
WORKFLOWS_DIR = SCRIPTS_DIR / "workflows"
CONFIG_PATH = SERVER_ROOT / "config" / "experiment_configs.json"

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def _experiments() -> dict:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return data.get("experiments") or {}


def _script_files() -> list[pathlib.Path]:
    """单项实验脚本。"""
    if not EXPERIMENTS_DIR.is_dir():
        return []
    return sorted(EXPERIMENTS_DIR.glob("*.qwf"))


def _workflow_files() -> list[pathlib.Path]:
    if not WORKFLOWS_DIR.is_dir():
        return []
    return sorted(WORKFLOWS_DIR.glob("*.qwf"))


def test_scripts_dir_exists():
    assert SCRIPTS_DIR.is_dir(), f"缺少实测脚本目录：{SCRIPTS_DIR}"
    assert _script_files(), "qwf_scripts/experiments/ 里一个 .qwf 都没有"
    assert _workflow_files(), "qwf_scripts/workflows/ 里一个 .qwf 都没有"


def test_script_set_matches_experiment_table():
    """脚本名 = 词表里的实验键（词表有 13 个，脚本就得有 13 份）。"""
    expected = set(_experiments())
    actual = {p.stem for p in _script_files()}
    assert actual == expected, (
        f"脚本集合与实验词表不一致："
        f"词表多出 {sorted(expected - actual)}；"
        f"脚本多出 {sorted(actual - expected)}"
    )


def test_workflow_names_do_not_collide_with_experiment_names():
    """`tools/qwf_exp.py` 按名字选脚本，两层重名会歧义。"""
    exp = {p.stem for p in _script_files()}
    wf = {p.stem for p in _workflow_files()}
    assert not (exp & wf), f"工作流和单项脚本重名：{sorted(exp & wf)}"


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


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.stem)
def test_workflow_lints_without_errors(path):
    from services.workflow_service.dsl.lint import lint_text

    report = lint_text(path.read_text(encoding="utf-8"))
    errors = report.get("errors") or []
    assert not errors, (
        f"{path.name} 有 lint error："
        + "; ".join(f"{e.get('code')} {e.get('message')}" for e in errors[:5])
    )


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.stem)
def test_workflow_runs_more_than_one_experiment(path):
    """工作流的意义就是"一条流程多个实验"。

    算的是**实际采集次数**：循环体里的 measure 按 repeat 的 max_rounds 折算
    （`tuneup_loop` 只有一条 measure，但要跑 3 轮，也是工作流）。
    """
    from services.workflow_service.dsl.textdsl.parser import parse

    ir = parse(path.read_text(encoding="utf-8"))

    def count(stmts, rounds=1):
        total = 0
        for st in stmts or []:
            if not isinstance(st, dict):
                continue
            op = st.get("op")
            if op == "measure":
                total += rounds
            elif op == "repeat":
                total += count(st.get("body"), int(st.get("maxRounds") or 1))
            else:
                for key in ("then", "else"):
                    if isinstance(st.get(key), list):
                        total += count(st[key], rounds)
        return total

    n_runs = count(ir.get("pipeline") or [])
    assert n_runs >= 2, (
        f"{path.name} 一条流程只采集 {n_runs} 次 —— "
        f"一条流程只干一件事应该放 experiments/ 而不是 workflows/"
    )


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.stem)
def test_workflow_keeps_running_when_one_experiment_fails(path):
    """工作流里每个 measure 都该 `onError continue`：一个失败不能带塌整条流程。

    例外：`repeat` 循环体里的 measure 故意不加 —— 循环体失败时 `stop_when`
    拿不到指标，比较会直接报"不是数值"，让测量失败把流程停下来更诚实。
    """
    from services.workflow_service.dsl.textdsl.parser import parse

    ir = parse(path.read_text(encoding="utf-8"))

    def walk(stmts, in_repeat=False):
        for st in stmts or []:
            if not isinstance(st, dict):
                continue
            op = st.get("op")
            if op == "measure":
                yield st, in_repeat
            elif op == "repeat":
                yield from walk(st.get("body"), True)
            else:
                for key in ("then", "else"):
                    if isinstance(st.get(key), list):
                        yield from walk(st[key], in_repeat)

    measures = list(walk(ir.get("pipeline") or []))
    assert measures, f"{path.name} 没有 measure 语句"
    missing = [st.get("as") for st, in_repeat in measures
               if not in_repeat and st.get("onError") != "continue"]
    assert not missing, (
        f"{path.name} 的 measure {missing} 没写 `onError continue` —— "
        f"一个实验失败会把整条流程带塌（那就不叫工作流了）"
    )

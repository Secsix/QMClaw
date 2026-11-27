#!/usr/bin/env python
"""qwf_gen.py - 按「实验 + 参数」生成 .qwf（文本 DSL）文件。

> 实现住在上游 OpenQWF（`vendor/OpenQWF/tools/qwf_gen.py`），本文件是**薄壳**：
> 委派上游生成器，并补一条本仓语义 —— `budget.max_datasets` **恒写**
> （上游只在 goal 模式写；lint 的 E_NO_BUDGET 要求 max_wall / max_writes /
> max_datasets 三个一起声明，少一个整份文件就红）。
> 改实现请改上游仓（`Secsix/OpenQWF`）并同步 submodule 指针，见 `docs/OPENQWF_SYNC.md`。

约定（2026-11-05 起）：跑量子测控实验一律走 `.qwf` 路径，不再直接 `POST :3003/execute`
下发裸 `sq.*` 代码。本工具负责把"我想跑 sq.t1，zpa 固定 0"这种意图写成可解析、
可静态校验、可干跑的 `.qwf`。

参数写法（`--param` 可重复），统一落进 `.qwf` 的 `args { }` 块 ——
`measure` 的字段白名单只有 `fn / qubit / args / params / reportDataset`
（外加通用的 `as / timeout / note / retries / onError`），直接写在测量块里会报 `E_UNKNOWN_FIELD`：
    zpa=0.0                 标量
    delay=0,2000,100       区间 → np.arange(0, 2000, 100)
    states=[0,1]            列表字面量
    update=false            布尔
    gate=X                  字符串

## goal 模式（P0，2026-11-05）

`--goal-objective` 会写出**目标契约**。两种写法（`--goal-style`）：

1. `native`（默认，P1 起）：直接写 `goal` 顶层块，**服务端 `normalize()` 负责 lowering**
   成 `repeat + if/else + assert`；执行器零改动：

       goal {
         objective   "复现 q2 的 iqraw 对比度"
         success { metric r1.contrast  operator >=  value 0.6 }
         max_rounds  3
         on_achieved "continue"
       }
       measure as c1 { ... }
       read    c1 as r1 { kind IQraw  threshold 0.4 }

2. `expanded`（P0 老写法）：手写展开低级节点，不依赖服务端 lowering：

    repeat max 3 stop_when r1.contrast >= 0.60 export r1 {
      measure as c1 { ... }
      read    c1 as r1 { kind IQraw  threshold 0.4 }
      say     "..."
    }
    assert r1.contrast >= 0.60 else halt as g1
    say     "GOAL reached: contrast=${r1.contrast} (>= 0.60)"

这正是 DSL 提案 §13.2 里 `goal {objective, success{...}, maxRounds}` 的 **lowering 结果**——
执行器完全不需要认识 `goal`，所以这一步零服务端改动。

语义要点（来自 `dsl/executor.py:423-451` `_exec_repeat`）：
- 每轮**开始**时清空体内绑定（S2：每轮重新绑定），但 `stop_when` 是在**本轮 body 跑完后**求值，
  所以它读得到**本轮**的 `$r1.*`，不需要读上一轮的值；
- 循环结束后，体内绑定默认被清掉 → 想让尾部的 `assert` 用到最后一轮的值，必须 `export r1`。

用法：
    python qwf_gen.py --fn sq.t1 --qubit q2 --param zpa=0.0 --param delay=0,2000,100 --timeout 120 --out t1_q2.qwf
    python qwf_gen.py --fn sq.ramsey_t2 --qubit q2 --read Ramsey --assert 'T2_us>=5' --out ramsey_q2.qwf
    python qwf_gen.py --fn sq.iqraw --qubit q2 --goal-objective "复现 q2 的 iqraw 对比度" --success-metric contrast --success-op ">=" --success-value 0.6 --max-rounds 3 --out iqraw_goal_q2.qwf
    python qwf_gen.py --help
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

import qwf_bridge  # noqa: E402

# 上游 argparse 没有 --max-datasets（本仓自己扩的一个参数），先从 argv 里摘出来
_DS_ARG = "--max-datasets"
_cli_max_datasets = ""

# budget 块后处理：上游只在 goal 模式写 max_datasets，且值固定为 max_rounds
_MAX_DATASETS_RE = re.compile(r"(?m)^    max_datasets .*\n")
_MAX_WRITES_RE = re.compile(r"(?m)^(    max_writes .*)$")

_MOD = None
_BUILD = None


def _max_datasets_value(args) -> str:
    """`max_datasets` 的取值：显式 `--max-datasets` > goal 模式的 `--max-rounds` > `--max-writes`。

    一次写最多产一个数据集，所以兜底取 `max_writes`。
    调用方（`checks/_qwf_gen_goal_check.py`）自建的 Namespace 不一定有
    `max_datasets` 键，所以用 `getattr` 兼容。
    """
    explicit = getattr(args, "max_datasets", "") or _cli_max_datasets
    if explicit:
        return str(explicit)
    if getattr(args, "goal_objective", ""):
        return str(max(1, int(getattr(args, "max_rounds", 1) or 1)))
    return str(getattr(args, "max_writes", "1"))


def _patch_budget(text: str, max_datasets: str) -> str:
    """把上游生成的 budget 块补成三件套齐全（max_wall / max_writes / max_datasets）。"""
    text = _MAX_DATASETS_RE.sub("", text)
    if not _MAX_WRITES_RE.search(text):
        return text
    return _MAX_WRITES_RE.sub(
        lambda m: m.group(1) + "\n    max_datasets " + max_datasets, text, count=1)


def _load():
    """加载上游生成器，并把它的 `build` 换成本仓带 budget 补丁的版本。"""
    global _MOD, _BUILD
    if _MOD is not None:
        return _MOD, _BUILD
    mod = qwf_bridge.load_tool("qwf_gen.py")
    upstream_build = mod.build

    def build(args) -> str:
        return _patch_budget(upstream_build(args), _max_datasets_value(args))

    mod.build = build
    _MOD, _BUILD = mod, build
    return _MOD, _BUILD


def build(args) -> str:
    """生成 .qwf 文本（`checks/_qwf_gen_goal_check.py` 直接调这个）。"""
    _mod, patched = _load()
    return patched(args)


def main() -> int:
    global _cli_max_datasets
    argv = sys.argv[1:]
    stripped = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == _DS_ARG and i + 1 < len(argv):
            _cli_max_datasets = argv[i + 1]
            i += 2
            continue
        if arg.startswith(_DS_ARG + "="):
            _cli_max_datasets = arg.split("=", 1)[1]
            i += 1
            continue
        stripped.append(arg)
        i += 1
    sys.argv = [sys.argv[0]] + stripped
    mod, _ = _load()
    return mod.main()


if __name__ == "__main__":
    raise SystemExit(main())

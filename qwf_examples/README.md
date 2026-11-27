# qwf_examples/ — QWF DSL 教学范例

QWF 是本项目的测控编排 DSL（真源在独立仓 [OpenQWF](https://github.com/Secsix/OpenQWF)，本仓通过
`vendor/OpenQWF` submodule 引用，见 [`docs/OPENQWF_SYNC.md`](../docs/OPENQWF_SYNC.md)）。

本目录放**带注释的教学范例**：每份文件开头都写清「这个例子演示什么、坑在哪」。
日常实验脚本在 [`qwf_scripts/`](../qwf_scripts/)，两者区别见该目录 README。

## 范例清单

| 文件 | 演示内容 | 关键点 |
|------|----------|--------|
| `01_iqraw_contrast.qwf` | 最小可用流程：一次实验 + 读结果 | budget 三件套必写；实验节点**不要**写 `do_plot` |
| `02_ramsey_t2.qwf` | 不写任何寄存器的实验 | `sq.ramsey_t2` 写寄存器为空；T2\* 要用 `analyze`（analysis 端拟合）拟合，`read` 只认 IQraw |
| `03_t1_fixed_zpa.qwf` | T1 衰减 | `sq.t1` 必须固定 `zpa`，否则展开成二维扫描；`delay` 用 `range` 语法 |
| `04_decide_branch.qwf` | LLM 决策 + 分支 | `decide` 的 `from` 元素必须带 `$`（`$r1`）；`mode "intent"` / `"analysis"` 含义不同 |
| `05_goal_loop.qwf` | 有界自主循环 | `goal` 块含 `success` 与 `max_rounds` 硬上限 |
| `06_qiskit_t1.qwf` | qiskit/Aer 后端跑一次 T1 | qiskit 是纯本地模拟，**不产生 DataVault 数据集**，故只保留 `measure` |
| `07_two_qubit_swap.qwf` | 双比特 SWAP | 多比特写法 `qubits ["q0","q1"]`；`qubit`（单数）是历史写法，两者不能同时写 |

## 运行

```bash
# 语法检查（CI 必跑，必须 0 error）
python qwf_lint.py qwf_examples/*.qwf

# 干跑，不碰硬件
python qwf_run.py qwf_examples/06_qiskit_t1.qwf --dry

# 真跑（走 qiskit 模拟）
python qwf_run.py qwf_examples/06_qiskit_t1.qwf --if yes
```

真机跑法（`01`–`05`）需要 LabRAD + `lqcs`，且会触发写闸门，详见
[`docs/REAL_MACHINE.md`](../docs/REAL_MACHINE.md)。

## 约定

- 范例里的 `qubit` / `session` 用占位值（`q0` / `demo_session`），跑之前改成自己的。
- **不要**在这些文件里写真实设备标定值或 API Key。
- 改完范例要跑 `python qwf_lint.py qwf_examples/*.qwf`，并跑 `python checks/run_all_checks.py`。

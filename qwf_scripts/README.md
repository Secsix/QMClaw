# qwf_scripts —— 可直接用命令跑的 qwf 流程 / 实验脚本

这里放的是**脚本本体**：`.qwf` 文件，跑法就一条命令。

```bash
python qwf_exp.py --list                        # 看有哪些（工作流 + 单项）
python qwf_exp.py calib_single                  # 只打印计划（默认不跑）
python qwf_exp.py calib_single --if yes         # 真跑
python qwf_exp.py --all --if yes                # 全跑 4 条工作流
python qwf_exp.py --all-experiments --if yes    # 全跑 13 个单项实验
python qwf_exp.py t1 swap --if yes              # 一次跑多个（名字可混着写）
```

走的是 qwf 那条唯一推荐入口，不另开捷径：

```
.qwf → /workflows/parse_qwf → validate → manifest → run_ir
     → task_queue 租约 → quantum_service /execute → 后端策略 → Aer
```

后端由 `quantum_service` 按 `fallback_config.json` 的 `order` 选：
`lqcs` 真机 → `qiskit`/Aer → `offline_sim` 离线回放。
**本机没接真机时自动落到 qiskit**（所以下面这些脚本在开发机上也能跑出数）。

## 分两层：workflows/ 与 experiments/

qwf 本来就是**面向工作流运行**的设计，所以主体是 `workflows/`：

| 层 | 目录 | 一份脚本 | 干什么用 |
|---|---|---|---|
| 工作流 | `workflows/` | **多个实验 + 门槛 + 分支 + 循环** | 跑一条完整工艺链（标定、体检、复测） |
| 单项 | `experiments/` | 一个实验 | 隔离失败、快速定位、单独复测一个实验 |

两层互不重名（`tests/test_qwf_scripts.py` 盯着），`qwf_exp.py` 按名字选脚本。

## workflows/ —— 4 条工作流

| 工作流 | 采集次数 | 内容 |
|---|---|---|
| `calib_single.qwf` | 5 | 单比特标定链：T1 → Ramsey → π 幅度 → 单发读出 → AllXY，每步一道门槛 |
| `calib_two_qubit.qwf` | 1–2 | SWAP 保真度 → **达标才**标 DRAG alpha（不达标就跳过，省机时） |
| `tuneup_loop.qwf` | 1–3 | 有界自主循环：T1 不到 60µs 就自动复测，最多 3 轮（`repeat … stop_when`） |
| `bench_all.qwf` | 13 | 全量体检：13 个实验一条流程跑完，逐项给门槛结果 |

### 工作流里用到的四个 DSL 机制

| 机制 | 写法 | 作用 |
|---|---|---|
| 容错 | `onError continue`（每个 measure） | 一个实验失败**不会**带塌整条流程，后面的照跑 |
| 门槛 | `assert $c1.metrics.T1 >= 0.00002 else continue as g_t1` | 比阈值，不达标留一条 FAIL 记录（`QUALITY GATE` 行） |
| 分支 | `if $g_ro.passed { … } else { … }` | 用门槛结果驱动：达标才做下一步，不达标就跳过并给建议 |
| 循环 | `repeat max 3 stop_when $c1.metrics.T1 >= 0.00006 export c1 { … }` | 有界自主复测，达标就停，硬上限 3 轮 |

### 三个必须知道的坑

1. **别在 `say` 里直接插指标**：`say "…${c4.metrics.readout_fidelity}"` 在 c4
   失败时会抛 `undefined ref`，**整条流程跟着挂**。指标一律走 `assert` 门槛读，
   再用 `$g.passed` 分支。（`onError continue` 只保证"继续往下走"，不保证"指标还在"。）
2. **双比特实验只写 `qubits`，不能再写 `qubit`** —— 校验阶段会拦
   （`E_SCHEMA measure 同时写了 qubit 和 qubits`）。`qubits` 列表只接受字面量，
   换比特要直接改文件里的 `["q0","q1"]`。
3. **`.qwf` 里的阈值先写普通小数**（`0.00002` 而不是 `2e-5`）—— 科学计数法是
   上游新加的（`96a2240`），**要 workflow_service 重启后**才会生效。
   服务启停由人控制，所以脚本先按老口径写。

## experiments/ —— 13 个单项脚本

| 脚本 | 实验函数 | 比特 | 主要指标 | 备注 |
|---|---|---|---|---|
| `t1.qwf` | `sq.t1` | 1 | `T1` | 显式给 `zpa 0.0` + `delay range`，否则真机耗时闸门会拦 |
| `ramsey.qwf` | `sq.ramsey_df` | 1 | `T2`、`detuning` | |
| `piamp.qwf` | `sq.piamp` | 1 | `pi_amplitude` | 会回写 `PiGate.amp`（校准类） |
| `xeb.qwf` | `sq.xeb` | 1 | `gate_fidelity`、`error_per_cycle` | 点数最多，耗时最长 |
| `single_shot.qwf` | `sq.single_shot` | 1 | `readout_fidelity` | 只有 qiskit 侧有同名函数 |
| `iqraw.qwf` | `sq.iqraw` | 1 | `SNR`、`separation` | |
| `allxy.qwf` | `sq.allxy` | 1 | `average_fidelity`、`leakage` | 只有 qiskit 侧有同名函数 |
| `spectroscopy.qwf` | `sq.spectroscopy` | 1 | `f10` | 厂商口径必须显式给 `freq`，否则 lint 报 `E_SPEC_NO_FREQ` |
| `pulsed_spec.qwf` | `sq.spectroscopy_iq` | 1 | `qubit_frequency` | |
| `s21.qwf` | `sq.s21` | 1 | `fread` | 会回写 `fread` |
| `s21_dis.qwf` | `sq.s21_dis` | 1 | `dispersive_shift` | 会回写 `fread`（mode=always） |
| `swap.qwf` | `sq.swap` | 2 | `swap_fidelity` | 双比特：`qubits ["q0","q1"]` |
| `drag_calibrate.qwf` | `sq.drag_calibrate` | 2 | `optimal_drag` | 双比特：`qubits ["q0","q1"]` |

这 13 个和 `config/experiment_configs.json` 里的 `experiments` 一一对应
（`tests/test_qwf_scripts.py` 会盯着这件事）。

## 参数口径：qiskit 口径 vs 真机口径

**脚本里的 `args` 是 qiskit/Aer 口径**（`points` / `df_hz` / `samples` /
`error_rate` …）—— 不在 qiskit spec 里的参数会被 `_qiskit_params_from_code`
过滤掉，所以多给几个厂商口径的参数不会把 qiskit 跑歪。

**上真机前要换口径**：厂商函数要的是 `zpa` / `delay` / `freq` 这类，
见 `qwf_examples/03_t1_fixed_zpa.qwf`（真机版 T1）和
`qwf_examples/01_iqraw_contrast.qwf`。

两个容易踩的点：

1. **`sq.t1` 不显式给 `zpa` + `delay` 会被耗时闸门拦下** —— 厂商默认会把
   扫描展开成 3216 点，估算耗时超预算，流程在下发前就 Halt。
   所以 `t1.qwf` 里 `zpa 0.0` + `delay range 0 2000 100` 是必需的，不是多余。
2. **双比特脚本里的比特名是写死的** —— 见上面"三个坑"第 2 条。

## lint 里的警告怎么看

脚本全部 `error=0`（CI 也会 lint）。剩下的 warning 都是**有信息量的**：

| 警告 | 含义 |
|---|---|
| `W_FN_NOT_ON_HARDWARE` | 这个函数真机 `sq_nodes` 里没有，只能在 qiskit 跑 |
| `W_WRITE_FN` / `W_UPDATE_DEFAULT_ON` | 该实验会自动回写寄存器（校准类），真机跑要写清单 + 票据 |
| `W_PROFILE_HARDCODED_QUBIT` | 双比特脚本写死了 `q0`/`q1`（见上） |

## 和 `qwf_examples/` 的区别

- `qwf_examples/` —— **教学用范例**：带注释、讲 DSL 语法，`06/07` 是 qiskit 与双比特的例子
- `qwf_scripts/` —— **实测用脚本**：参数调好、注释只讲怎么跑
- `qwf_gen.py` —— 按命令行参数**现生成**一份 `.qwf`（要临时改参数时用它）

## 跑完之后

`qwf_exp.py` 会打印一行一个脚本的汇总（状态 / 后端 / 指标 / 耗时），
工作流还会额外打印**门槛结果**（`PASS`/`FAIL`）和流程里的 `say` 行。
想要完整 JSON：

```bash
python qwf_exp.py --all --if yes --out %TEMP%\qwf_wf.json
python qwf_exp.py --all-experiments --if yes --out %TEMP%\qwf_exp.json
```

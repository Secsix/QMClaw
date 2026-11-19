# qwf_scripts —— 可直接用命令跑的测控实验脚本

这里放的是**实测脚本本体**：每份 `.qwf` 跑一个实验，参数已经调好（点��、
扫描区间、超时、预算都写死在文件里），跑法就一条命令：

```bash
python qwf_exp.py --list            # 看有哪些
python qwf_exp.py t1                # 只打印计划（默认不跑）
python qwf_exp.py t1 --if yes       # 真跑
python qwf_exp.py --all --if yes    # 全跑 13 个
python qwf_exp.py t1 swap --if yes  # 一次跑多个
```

走的是 qwf 那条唯一推荐入口，不另开捷径：

```
.qwf → /workflows/parse_qwf → validate → manifest → run_ir
     → task_queue 租约 → quantum_service /execute → 后端策略 → Aer
```

后端由 `quantum_service` 按 `fallback_config.json` 的 `order` 选：
`lqcs` 真机 → `qiskit`/Aer → `offline_sim` 离线回放。
**本机没接真机时自动落到 qiskit**（所以下面这些脚本在开发机上也能跑出数）。

## 13 个脚本

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
2. **双比特脚本里的比特名是写死的** —— DSL 的 `qubits` 列表只接受字面量
   （不允许 `$env.xxx`），所以换真机比特要直接改文件里的 `["q0","q1"]`。
   lint 会给 `W_PROFILE_HARDCODED_QUBIT` 警告，这是预期内的。

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

`qwf_exp.py` 会打印一行一个实验的汇总（状态 / 后端 / 指标 / 耗时）。
想要完整 JSON：

```bash
python qwf_exp.py --all --if yes --out %TEMP%\qwf_exp.json
```

# 可移植真机测试包（旧版 legacy API，已改走 .qwf）

本包是**旧版**单比特自动标定流水线。实验调用口径仍是**旧版 legacy**：

```python
sq.s21_zpa2d(qobj)                 # 首步：现场采 S21ZPA 地图
sq.spectroscopy(qobj, freq=..., spec_amp=..., update=...)
sq.iqraw(qobj)
sq.piamp(qobj, amp=2)
sq.pidf(qobj, df=0.08)
sq.pidf(qobj)
sq.ramsey_df(qobj)
sq.s21_dis(qobj)
sq.set_pi(qobj, gate='X')
sq.set_pi(qobj, gate='X/2')
sq.t1(qobj, zpa=selected_bias)
sq.ramsey_t2(qobj)
sq.xeb(qobj)
```

**不是**新版 v1.0 的 `(s, qobj)` / `piAmp` / `target_gate` 口径 —— 那套在 `D:\QMClaw\s21zpa\`，
不要和本包混用。`zpa_qwf_runner.step()` 会主动拒绝新版权参数名。

## 两条执行路线

| 路线 | `settings.execution_route` | 实验怎么跑 | 寄存器怎么写 |
|------|--------------------------|-----------|--------------|
| **qwf（默认）** | `"qwf"` | 生成 `.qwf` → :3008 `/workflows/parse_qwf` → `/workflows/validate` → `/workflows/run_ir` → workflow_service → quantum_service | :3003 `/qubit/set_params`（`.qwf` **没有**写寄存器的节点，`apply` 只记建议值、不下发硬件） |
| direct（旧版行为） | `"direct"` | 本进程里直调 `sq.<fn>(qobj, **args)` | `qobj.regs.bias_z` + `wait_for_bias` |

`.qwf` 下发出来就是 `sq.<fn>(<qubit>, **args)`（见 `dsl/backends/lqcs.py:build_experiment_code()`），
**与旧版直调等价**，所以换路线不会改变实验语义。

## 文件

至少复制以下文件到另一台电脑同一目录：

```text
portable_zpa_pipeline.py
zpa_calibration_pipeline.py
zpa_qwf_runner.py          # .qwf 生成 + 两条路线的执行层
zpa_features.py
classify_zpa_datavault.py
plan_zpa_bias.py
zpa_v2/model.json
zpa_lab_adapter_template.py
datavault_live.py
requirements-portable.txt
zpa_pipeline_manifest.example.json
test_zpa_qwf_offline.py     # 离线自检（不连服务、不碰硬件）
```

复制时保持 `zpa_v2/model.json` 的相对路径；或者在 manifest/命令中指定绝对模型路径。

## 安装

在另一台电脑使用与真机实验环境相同的 Python，不要覆盖实验室已有环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-portable.txt
```

如果真机的 `sq/qobj` 来自实验室专用包，不要从公共 pip 安装替代品；在已有实验环境中运行，并保证这个目录可导入你们的实验模块。
**qwf 路线不需要在本机 import `sq`** —— 实验由 :3008 的 workflow_service 调起。

## 真实代码修改点

从 `zpa_lab_adapter_template.py` 复制一个本机适配器，例如 `my_lab_adapter.py`。
模板里已经给出两个类，按路线选一个：

* `QwfLabAdapter`（qwf 路线，**推荐**）：registry 读写、bias 施加+回读、DataVault 指标
  都能直接跑；只有 **bias 上下限、频率单位、RF 关闭动作、指标定义** 要按实验室确认。
* `LabAdapter`（direct 路线）：旧版骨架，方法签名与 qwf 版一致，实验室自己实现。

两条路线的适配器接口完全一样，流水线不区分：

* `get_context(bit)`：qwf 路线返回 `(bit, session)`；direct 路线返回 `(qobj, sq)`；
* `preflight(bit, ctx, settings, biases)`：确认 bias 上下限、频率单位 GHz、`bias_z` 映射、
  `t1(zpa=selected_bias)` 含义、驱动/RF 限值、恢复能力；
* `snapshot/restore`：保存并恢复完整实验状态；
* `apply_bias(ctx, bias)`：**写 → 等稳定 → 回读核对**（不是 sleep），返回回读值；
* `read_bias(ctx)`：读当前 `bias_z`；
* `maximum_visibility(result, ctx)`：调用 `datavault_live.iqraw_visibility` 或实验室正式算法；
* `piamp_excited(result, ctx)`：调用 `datavault_live.pipulse_excitation` 或实验室确认的 P1 规则；
* `final_metrics(ctx, returns)`：从**本次**结果取读取保真度、T1、T2（注明类型）、比特频率；
* `safe_shutdown/restore`：异常和中断时安全关闭并恢复。

默认真机 DataVault 根目录为：`D:\DataVault\LQHL.dir\test.dir`。该目录包含按日期/设备划分的子目录，
实时结果发现会递归搜索子目录；适配器必须确认当前会话确实写入此目录。

真实 DataVault 文件会以以下字段产生：

```text
Spectroscopy: f0=freq, f6=P1
IQraw: f0=stats, f1=Is|I, f2=Qs|I, f3=Is|X, f4=Qs|X
PiPulse: f0=piAmp, f6=P1
PiPulse df: f0=df, f6=P1
Ramsey df: f0=delay
S21_dis: distance | 01
T1: 根据 Dependent label 寻找 P1
```

## π 与 π/2 命令

流程现在使用你确认的命令：

```python
sq.set_pi(qobj, gate='X')
sq.set_pi(qobj, gate='X/2')
```

不是 `set_pi_half`，也不是 `set_pi/2`。适配器中的兼容方法保留，但主流程不调用它。

## 先离线计划

把 manifest 中的 `out` 改成新目录，并填写每个比特的当前 S21ZPA 文件：

```json
{"bit":"q23_7", "s21zpa_file":"00731 - q23_7%c S21zpa2d.hdf5"}
```

运行：

```powershell
python portable_zpa_pipeline.py zpa_pipeline_manifest.example.json
```

离线模式只识别区间和生成 bias 序列，不操作硬件。
manifest 里的 `s21zpa_file` **只用于离线计划**；真机执行时 bias 区间以**现场采的那张**为准。

## 干跑（只生成 .qwf 并校验，不下发实验）

需要 :3008 在跑（只做 parse/validate，**不碰硬件、不写寄存器**）：

```powershell
python portable_zpa_pipeline.py actual_manifest.json --dry --adapter my_lab_adapter
```

产物在输出目录里：`q23_7_s21zpa2d.qwf`、`q23_7_probe_b*.qwf`、`q23_7_chain.qwf`。
`--dry` 只支持 qwf 路线（direct 路线会真的调实验）。

## 真机执行

必须在实验室已有真机环境中（或 :3008/:3003 服务在跑），先让适配器拿到上下文，再显式执行：

```powershell
python portable_zpa_pipeline.py actual_manifest.json --execute --adapter my_lab_adapter
```

执行前检查：

1. 先使用一个已确认的单比特和一个全新输出目录；
2. 确认 DataVault 根目录与当前实验会话一致；
3. 确认脚本能区分本次新文件与历史文件；
4. 确认 `Visibility` 与 `P1` 判据经过历史数据核验；
5. 确认设备 bias、RF 功率、频率范围和恢复流程；
6. 第一次实验建议把真实适配器的执行参数打印/记录，不要同时跑多比特；
7. **真机写闸门默认关闭**（`fallback_config.json` 的 `allow_set_params=false`），
   必须由人显式打开；驱动**绝不**绕过闸门。

流程为：

```text
【首步】sq.s21_zpa2d(qobj)        ← 现场采图，绑定本次 DataVault 结果
→ 从现场图识别可靠 ZPA 区间
→ 0.2 步长 bias_z（每个点：写 bias → 等稳定 → 回读）
→ spectroscopy / iqraw / piamp（一个 .qwf 一批）
→ Visibility > 0.05 且 P1 激发
→ 停止遍历
→ pidf(df=0.08)
→ pidf()
→ ramsey_df()
→ s21_dis()
→ iqraw()
→ set_pi(gate='X')
→ set_pi(gate='X/2')
→ t1(zpa=selected_bias)
→ ramsey_t2()
→ xeb()
→ iqraw()
→ 输出结果
```

bias 遍历阶段的 `spectroscopy/iqraw/piamp` 默认 **只采不写**（`settings.search_update=false`），
避免中途改写 f10 影响后续判据；`pidf` 等标定步骤仍按厂商默认回写寄存器。

实验异常、超时或中断会尝试关闭驱动并恢复快照；清理失败会停止后续批次。
不要强制杀 Python 进程替代适配器的安全停机。

## 测试

在没有真机（甚至没有服务）的电脑上：

```powershell
python -m py_compile zpa_calibration_pipeline.py zpa_qwf_runner.py zpa_lab_adapter_template.py
python test_zpa_qwf_offline.py     # .qwf 生成/DSL 校验/legacy 参数纪律/离线规划
```

（`test_zpa_pipeline.py` / `test_datavault_live.py` 在新版包 `D:\QMClaw\s21zpa\` 里，本包不带。）

`test_zpa_qwf_offline.py` 会用仓库里的 DSL（`Agentic Workflow/qmclaw-server/services/workflow_service/dsl`）
离线解析并校验生成的 `.qwf`；找不到 DSL 时自动跳过该段，其余检查照跑。
测试全部使用模拟数据，不会连接真机。

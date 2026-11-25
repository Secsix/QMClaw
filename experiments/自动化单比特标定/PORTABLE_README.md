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

**不是**新版 v1.0 的 `(s, qobj)` / `piAmp` / `target_gate` 口径 —— 那套在 `<仓库根>/s21zpa/`，
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

### session 名从哪来

`.qwf` 的 `env.session` 与 DataVault 子目录都由它决定，**必须与当前实验窗口一致**：

```powershell
(Invoke-WebRequest http://127.0.0.1:3003/health -UseBasicParsing).Content
```

取 `session_path` 的最后一段（例如 `20261113_20qubits`），填进 `adapter_config.session`。
注意 `config/session.json` 里的 `path` 可能是旧的，别拿它当准。

DataVault 数据集号在**每个 session 里重新编号**，所以适配器会优先锁定
`<datavault_root>/<session>.dir`，避免按根目录搜时撞上别的 session 的同号文件。

### 执行前检查

1. 先使用一个已确认的单比特和一个全新输出目录；
2. 确认 DataVault 根目录与当前实验会话一致；
3. 确认脚本能区分本次新文件与历史文件；
4. 确认 `Visibility` 与 `P1` 判据经过历史数据核验；
5. 确认设备 bias、RF 功率、频率范围和恢复流程；
6. 第一次实验建议把真实适配器的执行参数打印/记录，不要同时跑多比特；
7. **真机写闸门默认关闭**（`fallback_config.json` 的 `allow_set_params=false`），
   必须由人显式打开；驱动**绝不**绕过闸门。
   `allow_execute` 管实验下发，`allow_set_params` 管改寄存器（写 `bias_z`）——
   **后者不开，bias 遍历一定失败**。
8. 硬件 lane 并发是 1：别的实验在跑时，本流程会排队（workflow_service 租约）。

流程为：

```text
【首步】sq.s21_zpa2d(qobj)        ← 现场采图，绑定本次 DataVault 结果
→ 从 session 目录（D:\DataVault\LQHL.dir\test.dir\<session>.dir）读本次那张图
→ 识别有效 ZPA 区间（谐振轨迹的跨度）
→ 从区间最小值开始、0.2 步长均匀扫到区间最大值（中间不跳空隙）
   每个点：写 bias → 等稳定 → 回读
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

bias 遍历从**有效区间的最小值**起步、0.2 V 等步长走到区间最大值，中间不跳空隙：
S21ZPA 窗口只有 fread 附近 2 MHz，读出腔扫出窗口只说明"谐振离开这个窗口"，
不代表比特不可用（spectroscopy 扫 350 MHz，比特跃迁仍可能在窗口里）；人工标注也是
按"谐振轨迹的跨度"给的有效区间（00049 两支之间夹平层仍标成 (-2.6, 3)）。
若只想扫"看得见谐振"的那些段，用 `bias_grid(intervals, step, span=None)` 的老口径
（各分段各自起步、不跨空隙）。

bias 遍历阶段的 `spectroscopy/iqraw/piamp` 默认 **只采不写**（`settings.search_update=false`），
避免中途改写 f10 影响后续判据；`pidf` 等标定步骤仍按厂商默认回写寄存器。

实验异常、超时或中断会尝试关闭驱动并恢复快照；清理失败会停止后续批次。
不要强制杀 Python 进程替代适配器的安全停机。

### 开跑前先预览流程（不碰真机）

`test_zpa_flow_preview.py` 把**真正会下发的调用序列**打印出来（离线判定 + 阶段 0/1/2
的每个 legacy 调用、参数、点估计、闸门与服务状态），确认无误再 `--execute`：

```powershell
python test_zpa_flow_preview.py                       # 预览 q7_21
python test_zpa_flow_preview.py --manifest m.json --bit q7_21
python test_zpa_flow_preview.py --execute             # 真机执行（同一套步骤）
```

预览里会直接看到：离线区间判出来的 bias 候选（q7_21 = −3.0…3.0 共 31 点，中间那
13 个点在 S21ZPA 图上是平层，但比特跃迁仍可能在 spectroscopy 窗口里）、
每个 bias 的 spectroscopy/iqraw/piamp、标定链 11 步，以及 `allow_execute` /
`allow_set_params` / 服务端口是否在线。注意 `offline_mode=auto` 时 LabRAD 掉线会
**悄悄用历史数据**顶替，真机跑前建议先切 `online`。

## ZPA 区间怎么判（zpa_interval.py + 训练出来的模型）

S21ZPA 图 = 逐层（每个 zpa 一层）扫一条 S21-vs-频率 曲线。**旧规则**是"每层取全局
argmin + 局部 find_peaks prominence ≥ 1 dB"，在真机数据上有三个硬伤：

1. **宽 dip 被误杀**：prominence 是跟相邻点比的，0.2 MHz 栅格下宽 dip 的局部
   prominence 天然≈0。真机 q7_21 图上 -1.4 层 dip 深 5 dB，旧规则只给 0.94 dB。
2. **同层多 dip 会跳**：一层里同时有读出腔和比特跃迁时，逐层 argmin 在两条分支间
   跳，区间被切成碎片（q21_5 的 500 点图上能看到 3 条分支在 ~4/46/89 MHz 处交替）。
3. **谐振扫出窗口就报废**：dip 落到窗口边界就判 `frequency_window_edge`，明明是
   谐振走出了读出窗口，判据却当成"数据不可用"。

### 人工标注的口径（这是模型的判据来源）

现场人工看图标注了 5 张图（`zpa_interval_labels.json`）：

| 文件 | 比特 | 人工标注的有效区间 |
|---|---|---|
| 00044 | q21_5 | (-2, 2) |
| 00047 | q11_15 | (-2, 1) |
| 00049 | q13_13 | (-2.6, 3) |
| 00055 | q11_15 | (-3, 2) |
| 00059 | q19_7 | (0, 3) ← **与数据不符，见下** |

口径 = **谐振轨迹在频率窗口内的跨度**（不是逐层 dip 强度）：

- 谐振从窗口一侧扫入、扫过、再扫出 → 这段 zpa 都算有效；
- **窗口边缘上的 dip 也要算**（它就是谐振进出窗口的证据）；
- 中间"平层"（dip 太浅的那几层）**也属于有效区间** → 区间取各段的跨度（hull），
  不是把区间切成碎片。00049 就是两支之间夹着 5 层平层，人工仍然标成 (-2.6, 3)。

### 训练出来的模型（`zpa_interval_model.json`）

`train_zpa_interval.py` 用这 5 张标注做网格搜索 + 留一交叉验证，产出：

```
depth_rel = 0.12      显著 dip：depth >= max(depth_floor_db, depth_rel × 本文件最大深度)
depth_floor_db = 2.0  绝对下限（dB），挡住噪声层的假 dip
tol_mhz = 2.0         相邻两层的 dip 频率差 ≤ 2 MHz 才算同一条轨迹
min_points = 2        一条轨迹至少覆盖 2 层才算一个区间
max_gap = 0           不跨层跳过 dip 太浅的层（宁可切成两段，也不要串错）
```

显著性必须**相对该文件的最大深度**：不同比特的对比度差一个量级
（7.4 dB ~ 41 dB），绝对阈值跨文件不通用。

留一验证（用 4 张选参数、第 5 张上测，端点误差按 zpa 栅格折成"层数"）：

| 留出 | 人工 | 预测 | 误差 |
|---|---|---|---|
| 00044 | (-2, 2) | (-1.4, 1.4) | 3.0 层 |
| 00047 | (-2, 1) | (-2.2, 1.4) | 1.5 层 |
| 00049 | (-2.6, 3) | (-3.0, 3.0) | 1.0 层 |
| 00055 | (-3, 2) | (-3.0, 1.8) | 0.5 层 |
| 00059 | (0, 3) | (0.2, 1.8) | 3.5 层 |

平均 **1.9 层**（zpa 步长 0.4 V ⇒ 约 0.76 V）。全量训练下 4 张与人工相差 ≤ 1 层。
模型文件里另存了训练时的 200 组次优参数（`ensemble`），运行时用来报
`ensemble_agreement`（参数轻微变化时结论是否稳定），**不参与判区间**。

### ⚠ 一条标注与数据冲突（00059 / q19_7）

人工标注 (0, 3)，但数据里 zpa = 2.2 / 2.6 / 3.0 三层是**平层**（深度
0.75~1.09 dB、无 dip），和 00047 里被判为无效的 1.8~3.0 平层特征完全一样，
无法用任何特征区分；谐振实测只在 **0.2 ~ 1.8**。把这张图排除后重新训练，
模型仍然给 (0.2, 1.8)，说明是**标注本身离群**，不是模型问题。
`test_zpa_trained_model.py` 会把它标成 WARN 等现场复核。

### 离线复核

```powershell
python train_zpa_interval.py            # 重新训练（默认读 DataVault session 目录）
python test_zpa_trained_model.py         # 验收：运行时结果 vs 人工标注
python validate_zpa_intervals.py         # 整目录新旧规则对比（只读）
```

当前 session（20261113_20qubits）28 张图：**旧规则 22 张有区间 → 新规则 27 张有区间**。
q7_21（`00088`）新规则给出 **[-3.0, -0.2]（8 层，读出腔支）+ [2.6, 3.0]（2 层，
比特跃迁支）** —— 这两支正是 00049 那种"两支夹着平层"的结构，所以要分开扫，
中间 -0.2~2.6 不扫。

采集侧的参数含义（厂商 `sq.s21_zpa2d`）：

- `zpa` 轴：`$arange` → `np.arange(lo, hi, step)`，**单位 V**；本包默认 -3 → 3.1、
  步长 0.2（31 层）。
- 频率轴：`np.arange(fread - freq_span/2, fread + freq_span/2, 0.0002)`，
  **单位 GHz**（`convertUnits(freq='GHz')`）。`0.0002 GHz = 0.2 MHz = 200 kHz`，
  不是 Hz；步长是写死的，与 `freq_span` 无关。`freq_span=0.002` → ±1 MHz →
  11 个频点。厂商默认 zpa 步长是 0.4（16 层），本包按现场习惯用 0.2。

## 测试

在没有真机（甚至没有服务）的电脑上：

```powershell
python -m py_compile zpa_calibration_pipeline.py zpa_qwf_runner.py zpa_lab_adapter_template.py zpa_interval.py
python test_zpa_qwf_offline.py     # .qwf 生成/DSL 校验/legacy 参数纪律/离线规划
python test_zpa_interval_offline.py # ZPA 区间判定：合成图 + 真机历史图
python test_zpa_trained_model.py   # 训练出来的模型 vs 人工标注（验收）
```

（`test_zpa_pipeline.py` / `test_datavault_live.py` 在新版包 `<仓库根>/s21zpa/` 里，本包不带。）

`test_zpa_qwf_offline.py` 会用仓库里的 DSL（`server/services/workflow_service/dsl`）
离线解析并校验生成的 `.qwf`；找不到 DSL 时自动跳过该段，其余检查照跑。
测试全部使用模拟数据，不会连接真机。

`test_zpa_interval_offline.py` 用合成 S21ZPA 图覆盖四类失效模式（移动谐振、宽 dip、
同层双 dip、纯噪声、谐振在窗口外），并在 DataVault 在场时顺带检查 q7_21 的历史图。
`test_zpa_trained_model.py` 用现场人工标注的 5 张图验收训练出来的模型。
`validate_zpa_intervals.py` 拿整目录历史图对比新旧两套规则（只读，不写硬件）。

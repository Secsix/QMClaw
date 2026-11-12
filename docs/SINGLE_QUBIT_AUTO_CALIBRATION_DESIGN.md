# 自动化单比特标定优化：可实现方案与脚本架构

> 本文根据 `C:\Users\lqcs\Desktop\曙光测试日志\单比特自动化测试优化` 中的 PDF、XMind 和配套图片资料整理，并结合 QMClaw 现有 `sq.*` 实验封装、离线指标分析与 S21 图片读数工具，形成可落地的工程方案。

## 1. 资料中明确的核心思想

原始方案的主线是：**先从已有 S21 测试图获得比特编号和读取腔参考频率，再在每个比特的 ZPA 范围内搜索活性点；一旦满足 IQraw 与 PiAmp 的判活条件，就停止该比特的循环扫描；对活比特执行针对性参数优化，并根据最终 IQraw 结果选择后续读取优化策略。**

XMind 中明确给出的实验链路：

1. 输入之前的 S21 测试结果照片，输出比特号与 S21 参考值。
2. `qobj.regs.fread = S21参考值`；以参考值附近约 ±20 MHz 的范围执行 `sq.s21`，拟合出 1～2 个候选读取腔频率。
3. 分别尝试候选频率执行 `sq.s21_zpa`，使用 ZPA 识别脚本确定每个比特的 ZPA 范围。
4. 在每个比特的 ZPA 范围内，以步长 0.2 扫描 spectroscopy；每个点执行 `sq.iqraw` 判断读取活性，并执行 `sq.piamp` 判断驱动活性。
5. 当 `iqraw Maximum Visibility > 0.05` 且 `piamp` 能够激发时，停止该比特的循环实验，保存当前参数作为候选工作点。
6. 对活性参数进行后续优化：`sq.pidf(qobj, df=0.08)`、`sq.pidf(qobj)`、`sq.ramsey_df(qobj)`、`sq.s21_dis(qobj)`、`sq.iqraw(qobj)`、`sq.set_pi(qobj)`、`sq.set_pi/2(qobj)`、`sq.t1(qobj, zpa=x)`，再依据 IQraw 结果给出读取优化策略。

注意：仓库中另有离线体检工具使用 `snr > 0.1` 作为可复现判活口径、Rabi swing ≥ 0.15 且 ≥ 4σ 作为 PiAmp 正常判据；正式上线时应把原始资料阈值与离线体检阈值做成配置项，而不是硬编码。

## 2. 可落地的总体流程

```text
S21照片/历史结果
    ↓
照片解析：qubit_id + fread_ref + 置信度
    ↓
初始化并校验寄存器（仅读取；写入需通过写闸门）
    ↓
局部 S21：fread_ref ± 20 MHz
    ↓
腔频候选拟合（保留 1~2 个候选）
    ↓
候选腔频 × ZPA 探测，识别 zpa_min/zpa_max
    ↓
ZPA 网格扫描：每点 spectroscopy → iqraw → piamp
    ↓
判活：visibility/snr + piamp/Rabi；通过即早停
    ↓
活比特定向优化：频率、读出、π/π/2、T1/T2
    ↓
最终复测与质量门禁
    ↓
只读报告 + 待写参数计划；人工批准后写入
```

### 2.1 两阶段搜索，避免不必要实验

- **阶段 A：粗定位**：以 ZPA 网格步长 0.2 扫描；每点只执行最少实验，优先 `spectroscopy`、`iqraw`，只有 spectroscopy/IQ 有响应时才执行完整 `piamp`。
- **阶段 B：局部精调**：在最佳 ZPA 附近以更小步长（建议 0.05～0.1，最终由配置决定）重测；只在候选点运行 Ramsey、S21_dis、T1 等耗时实验。
- **早停**：满足判活条件后不继续扫描；但保留“至少一次重复验证”选项，避免偶然噪声触发早停。
- **失败回退**：腔频拟合失败、ZPA 无响应、实验超时或结果不可信时，不自动扩大硬件写入范围；记录原因并转入人工复核或受限重试。

### 2.2 推荐判定口径

| 层级 | 指标 | 默认建议 | 说明 |
|---|---|---:|---|
| 读取活性 | Maximum Visibility | > 0.05（原始方案） | 用于快速早停 |
| 离线稳健判定 | IQraw SNR | > 0.1 | 与现有 health report 一致 |
| Rabi 正常 | 峰谷 swing | ≥ 0.15 | 避免仅凭拟合成功 |
| Rabi 显著性 | swing / σ | ≥ 4 | σ 用差分 MAD 估计 |
| 频率收敛 | Ramsey detuning | < 0.1 MHz | 进入最终合格状态的门禁 |
| 读出质量 | fidelity/visibility | 设备配置 | 不同设备需单独标定 |

建议把 `fast_alive` 与 `final_quality` 分开：前者用于搜索早停，后者用于最终交付，不能将 0.05 的快速阈值直接当作最终质量标准。

## 3. 脚本工程架构

建议新建独立目录 `tools/single_qubit_auto_calibration/`，在线执行与离线分析分层，避免分析脚本直接触碰真机。

```text
tools/single_qubit_auto_calibration/
├── README.md                         # 用法、风险、参数和故障排查
├── pyproject.toml / requirements.txt # 依赖声明
├── config/
│   ├── default.yaml                  # 阈值、扫描范围、超时、重试
│   └── device_profiles/*.yaml        # 不同芯片/线缆/频段配置
├── cli.py                            # 命令行入口：plan/run/analyze/report
├── models.py                         # Qubit、Candidate、Measurement、Decision 数据模型
├── config_loader.py                  # 配置加载、单位校验、默认值合并
├── safety.py                         # 写闸门、范围限制、人工批准、dry-run
├── state_machine.py                  # 状态机、断点续跑、重试与回退
├── workflow.py                       # 单比特/批量工作流编排
├── scheduler.py                      # 实验队列、超时、取消、面板并行
├── adapters/
│   ├── sq_adapter.py                 # sq.* 实验统一调用接口
│   ├── labrad_adapter.py             # LabRAD/DataVault 连接与参数读写
│   └── mock_adapter.py               # 离线模拟、单元测试
├── experiments/
│   ├── s21.py                        # 局部 S21、腔频候选
│   ├── zpa_probe.py                  # ZPA 探测与范围识别
│   ├── spectroscopy.py               # 频谱扫描与 f10 拟合
│   ├── iqraw.py                      # IQ 采集与 visibility/SNR
│   ├── piamp.py                      # Rabi/PiAmp 扫描
│   ├── ramsey.py                     # 失谐与 T2* 优化
│   ├── readout.py                    # S21_dis、读取频率和功率优化
│   ├── pulse.py                      # set_pi、set_pi/2、DRAG/alpha
│   └── relaxation.py                 # T1/T2 复测
├── analysis/
│   ├── s21_fit.py                    # 谐振腔波谷/峰拟合
│   ├── zpa_classifier.py             # ZPA 活性区间识别
│   ├── spectroscopy_fit.py           # f10 峰提取
│   ├── iq_metrics.py                 # 两态 IQ 分离、visibility、SNR、fidelity
│   ├── rabi_metrics.py               # swing、显著性、pi_amp
│   ├── ramsey_fit.py                 # detuning、T2*
│   ├── quality_gate.py               # 快速判活和最终质量门禁
│   └── strategy.py                   # 根据失败模式选择后续优化动作
├── input/
│   ├── s21_photo.py                  # S21 图片输入适配
│   └── manifest.py                   # CSV/JSON 手工输入与校验
├── persistence/
│   ├── run_store.py                  # JSON/SQLite 运行状态
│   ├── datavault_reader.py            # 离线 HDF5 读取
│   └── artifacts.py                  # 原始数据、拟合结果、图和日志
├── reporting/
│   ├── report.py                      # Markdown/CSV/JSON 报告
│   ├── assignment.py                  # 生成 regs.* 写入计划，不直接写硬件
│   └── plots.py                       # 曲线、判定点、失败原因图
└── tests/
    ├── test_s21_fit.py
    ├── test_zpa_classifier.py
    ├── test_iq_metrics.py
    ├── test_rabi_metrics.py
    ├── test_state_machine.py
    ├── test_safety.py
    └── fixtures/
```

## 4. 各部分代码职责

### 4.1 `cli.py`

提供四种模式：

```text
plan    只解析输入并生成实验计划，不采数、不写寄存器
run     按计划执行实验，默认 update=False、dry_run=True
analyze 读取已有 DataVault/JSON 结果，重新计算指标
report  输出 CSV/Markdown/JSON 和待写参数脚本
```

CLI 必须显式要求 `--allow-execute` 才能发起真机实验；参数写入还需单独的 `--allow-set-params`。默认只读。

### 4.2 `models.py`

使用 dataclass 或 Pydantic 定义：

- `QubitInput`：`qubit_id`、`fread_ref`、来源图片、置信度。
- `ScanPoint`：`zpa`、`fread`、`f10`、扫描阶段。
- `ExperimentResult`：实验名、dataset、参数快照、原始数据路径、质量分数、错误。
- `CandidateWorkingPoint`：候选寄存器、判活指标、来源记录。
- `CalibrationState`：当前状态、尝试次数、下一个动作、失败原因。
- `CalibrationSummary`：最终参数、门禁结论、建议策略、人工确认状态。

### 4.3 `input/s21_photo.py`

复用现有 `tools/s21_photo_reader` 思路：OCR 读取 `qXX_X` 标签和波谷频率，坐标轴标定后生成 `QubitInput`。必须保留 OCR 置信度、几何反算值和人工复核标志。图片读数只作为初始参考，不能直接等价为最终 `regs.fread`。

### 4.4 `adapters/sq_adapter.py`

只负责把统一实验请求翻译为现有调用：

```python
run("s21", qobj, freq=freq, update=False, do_plot=False)
run("spectroscopy", qobj, freq=freq, update=False, do_plot=False)
run("iqraw", qobj, do_plot=False)
run("piamp", qobj, amp=amp, update=False, do_plot=False)
run("ramsey_df", qobj, do_plot=False)
run("s21_dis", qobj, update=False, do_plot=False)
run("set_pi", qobj, gate="X", update=False)
run("t1", qobj, zpa=zpa, do_plot=False)
```

真实函数签名必须由目标环境确认；适配层不得在模块导入时连接 LabRAD。每次调用要记录输入参数、开始/结束时间、dataset、异常和超时。

### 4.5 `experiments/`

每个文件只做三件事：构造扫描参数、调用 adapter、把结果交给 analysis。实验模块不作最终决策，也不直接修改寄存器。

- `s21.py`：围绕 `fread_ref` 生成 ±20 MHz 频点，识别 1～2 个谐振候选。
- `zpa_probe.py`：对候选腔频率进行 ZPA 探测，返回连续活性区间和可信度。
- `spectroscopy.py`：在当前 ZPA 位置寻找 f10，并保存拟合峰。
- `iqraw.py`：提取 Maximum Visibility、sep、SNR、norm、fidelity。
- `piamp.py`：执行 PiAmp/Rabi 并提取峰、谷、最佳 pi 振幅。
- `ramsey.py`：测量失谐和 T2*，提供下一次频率修正量。
- `readout.py`：S21_dis 优化 `fread`、读取功率和色散分辨率。
- `pulse.py`：调用 `set_pi`/`set_pi/2`，必要时加入 alpha/DRAG 精调。
- `relaxation.py`：在候选 ZPA 或局部 ZPA 网格测 T1/T2，作为最终质量检查。

### 4.6 `analysis/quality_gate.py`

统一所有判定，避免阈值散落：

```python
fast_alive = (
    metrics.maximum_visibility > cfg.fast_visibility_min
    and metrics.piamp_excited
)
final_alive = (
    metrics.iq_snr > cfg.iq_snr_min
    and metrics.rabi_swing >= cfg.rabi_swing_min
    and metrics.rabi_significance >= cfg.rabi_sigma_k
    and abs(metrics.ramsey_detune_mhz) < cfg.max_detune_mhz
    and metrics.t1_us >= cfg.min_t1_us
)
```

异常数据（单位错误、fread 超界、拟合不收敛、数据点不足）必须产生结构化 `quality_flags`，不能静默接受。

### 4.7 `analysis/strategy.py`

将失败模式映射为动作，先规则后优化：

| 现象 | 下一步动作 |
|---|---|
| S21 无明显腔谷 | 换候选腔频；扩大局部窗口前先人工确认 |
| ZPA 全区无响应 | 检查 ZPA 范围、参考腔频、接线与功率 |
| IQraw visibility 低 | 先做 `s21_dis`/读取功率优化，再复测 IQraw |
| IQraw 好但 PiAmp 无振荡 | 重新 spectroscopy / 调整 drive power / PiAmp 扩大范围 |
| Ramsey detune 超标 | `sq.pidf(df=0.08)` 或 `sq.ramsey_df`，更新 f10 后复测 |
| T1 低 | 扫 zpa 找较优点，检查 sweet spot 和耦合 |
| 读出分辨率低 | 优化 fread、读取功率、ring power 与判别器 |

规则引擎先决定安全且有限的候选动作；后续可用历史数据学习动作优先级，但不能让大模型直接绕过安全门禁写硬件。

### 4.8 `state_machine.py` 与 `workflow.py`

推荐状态：

```text
INIT
→ INPUT_VALIDATED
→ CAVITY_CANDIDATES_FOUND
→ ZPA_RANGE_FOUND
→ SEARCHING_ACTIVE_POINT
→ ACTIVE_POINT_FOUND
→ F10_OPTIMIZED
→ READOUT_OPTIMIZED
→ PI_OPTIMIZED
→ FREQUENCY_TUNED
→ RELAXATION_VERIFIED
→ FINAL_VERIFIED
→ COMPLETE / NEEDS_REVIEW / FAILED
```

每个状态定义：进入条件、实验动作、成功指标、最大重试次数、回退状态、失败原因。工作流支持保存断点，服务/进程中断后可以从最后一个已确认状态继续，而不是重复所有真机实验。

### 4.9 `safety.py` 与写回设计

必须区分三种动作：

1. **读测量**：可自动执行，但仍需超时、取消和硬件范围限制。
2. **实验参数临时设置**：默认只在 qobj 内存态生效，`update=False`。
3. **持久化写寄存器**：生成待写计划，展示 diff、范围和证据；人工确认后才执行，写后回读校验。

禁止脚本自动执行不可逆或范围不明的写入。对 `f10`、`fread`、`bias_z`、PiGate 参数分别配置允许范围；越界直接 `NEEDS_REVIEW`。

## 5. 推荐一次运行的伪代码

```python
def calibrate_one(inp, cfg, adapter):
    ctx = RunContext.create(inp, cfg)
    state = StateMachine.load_or_create(ctx)

    candidates = s21.find_candidates(adapter, inp.fread_ref, cfg.s21)
    if not candidates:
        return fail(ctx, "NO_CAVITY")

    zpa_ranges = zpa_probe.find_ranges(adapter, candidates, cfg.zpa)
    if not zpa_ranges:
        return fail(ctx, "NO_ZPA_RANGE")

    for point in zpa_scan(zpa_ranges, cfg.zpa):
        spec = spectroscopy.measure(adapter, point, cfg.spectroscopy)
        if not spec.valid:
            continue
        iq = iqraw.measure(adapter, point, cfg.iqraw)
        if not quality_gate.iq_fast_pass(iq, cfg):
            continue
        rabi = piamp.measure(adapter, point, cfg.piamp)
        decision = quality_gate.fast_alive(iq, rabi, cfg)
        persist_candidate(ctx, point, spec, iq, rabi, decision)
        if decision.pass_ and cfg.early_stop:
            break

    candidate = choose_best_candidate(ctx)
    if candidate is None:
        return review(ctx, "NO_ACTIVE_POINT")

    candidate = strategy.optimize_frequency(adapter, candidate, cfg)
    candidate = strategy.optimize_readout(adapter, candidate, cfg)
    candidate = strategy.optimize_pi(adapter, candidate, cfg)
    final = strategy.verify(adapter, candidate, cfg)
    return report(ctx, final)
```

## 6. 输出物与审计

每个运行目录建议包含：

```text
runs/<run_id>/
├── input.json                 # 输入比特、图片、参考值
├── config.snapshot.yaml       # 本次实际配置快照
├── plan.json                  # 计划的实验与依赖
├── events.jsonl               # 状态、动作、错误、人工批准记录
├── raw/                       # 原始数据或 DataVault 引用
├── metrics.json               # 每次实验的指标
├── candidates.json            # 候选工作点及淘汰原因
├── report.md
├── summary.csv
├── assignment_plan.py         # 仅生成，不自动执行
└── plots/                     # S21/IQ/Rabi/Ramsey/T1 图
```

报告至少给出：比特号、输入来源、最终 `fread/f10/f21/bias_z`、IQ 指标、PiAmp 指标、Ramsey detune、T1/T2、执行过的实验、判定阈值、失败与重试次数、是否需要人工确认。

## 7. 与当前 QMClaw 的复用关系

- `backends/lqcs/experiments.py` 已有 `sq.s21`、`sq.iqraw`、`sq.piamp`、`sq.ramsey_df`、`sq.s21_dis`、`sq.t1`、`sq.pidf`、`sq.set_pi` 等统一实验配置，可作为 adapter 的基础。
- `tools/qubit_health_report/metrics.py` 已有 IQraw、Rabi、Ramsey、Spectroscopy、S21_dis 的可复现指标实现，应抽取/复用到新模块，避免重复定义。
- `tools/qubit_health_report/health_report.py` 已实现按比特聚合 DataVault、选择工作点、输出 CSV/Markdown 的模式，可作为离线分析和回放模式基础。
- `tools/s21_photo_reader/` 已实现 S21 图片 OCR、坐标标定、比特/波谷配对、CSV/Markdown 输出和 LabRAD 写入脚本生成；自动标定只应复用其“读数与校验”部分，不应默认直接执行写入。
- `skills/qubit-calibration/SKILL.md` 与 `skills/qmclaw-tuneup-workflow/SKILL.md` 给出了更完整的 13/15 步标准流程和质量门禁，可作为后续扩展的高级验证阶段。

## 8. 分阶段实施建议

### Phase 0：离线回放（必须先做）

- 输入历史 DataVault 和 S21 图片。
- 不连接 LabRAD、不写寄存器。
- 验证腔频候选、ZPA 区间、判活、工作点选取和报告。
- 固化阈值与单元测试。

### Phase 1：单比特在线只读实验

- 只执行 `update=False` 的 S21、spectroscopy、IQraw、PiAmp。
- 实现超时、取消、断点与原始数据留存。
- 人工检查实验顺序、dataset 和曲线。

### Phase 2：受控参数更新

- 先仅生成 `assignment_plan.py`，人工审核 diff。
- 打开写闸门后一次只写一个比特，写后回读并记录。
- 先允许 `fread`，再逐步开放 `f10`、`bias_z`、PiGate 参数。

### Phase 3：优化策略与批量调度

- 规则策略稳定后再加入历史动作预测/Markov 优先级。
- 面板内并行必须经过资源冲突检查；面板之间顺序执行。
- 增加 XEB、DRAG、全量 T1/T2 作为最终验收，而不是搜索阶段的必跑实验。

## 9. 关键风险与必须澄清项

1. 原始资料的 `Maximum Visibility > 0.05` 与现有工具的 `SNR > 0.1` 不是同一指标，必须在设备数据上做映射和回归验证。
2. `sq.s21_zpa`、`sq.s21_zpa2d`、`sq.t1(zpa=x)` 的实际签名和单位要在目标测控环境确认；设计中不能假定所有版本一致。
3. S21 图片读数存在 OCR 和坐标标定误差，必须保留置信度，并支持人工修订参考值。
4. “piamp 能够激发起来”必须量化为 Rabi 峰谷和显著性，而不是仅以函数没有抛异常判定成功。
5. `update=True`、寄存器持久化和写闸门的边界必须由操作者显式确认；默认只读。
6. 资料中给出的具体 ZPA 范围、频率范围和步长适用于当时设备，不能无条件迁移到新芯片；应放进 device profile。

## 10. 结论

这套优化最适合实现为“**图片/历史输入 + 局部实验搜索 + 规则质量门禁 + 目标性优化 + 人工批准写回**”的状态机，而不是一个把所有实验串起来的线性脚本。第一版应优先完成离线回放和单比特只读在线版本；等阈值、单位、拟合与早停逻辑经过历史数据验证后，再开放分级写入。这样既保留原方案通过 ZPA 网格快速寻找活比特、满足条件即早停的效率优势，也符合 QMClaw 现有真机写闸门和离线分析架构。

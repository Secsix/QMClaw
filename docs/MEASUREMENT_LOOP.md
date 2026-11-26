# 测控循环（状态驱动）

这份文档讲一件事：**怎么让一个比特从 `unknown` 走到 `characterized`，而下一步做什么不是写死的。**

对应脚本：`server/tools/measurement_loop.py`
相关：`docs/CONTROL_CONSOLE_DEMO.md`（控制台怎么演示）、`_local/docs/STATE_MACHINE_DESIGN.md`（状态机设计，私有）

## 1. 它在四层里的位置

| 层 | 干什么 | 落在哪 |
|----|--------|--------|
| 驾驭层 | 提出「做一轮测控」，在需要判断时拍板 | DSH 侧（skill `qmclaw-measurement-loop` + 本脚本） |
| 机制层 | 状态机 + Router：当前态、合法边、闸门、模板 | `server/services/common/{qubit_state,state_machine,router}.py` |
| 执行层 | 一切实验落成 QWF 再提交 | `server/services/workflow_service/`（`state_route` 节点） |
| 证据层 | 记录 → 证据 → 状态重判，不可变 | `server/services/common/{runrecord,state_evidence,evidence_store}.py` |

循环只碰两头：**向机制层要判定**、**向执行层提交 QWF**。它自己不计算状态、不写判据。

## 2. 每轮四步

```
① POST /api/quantum/qubit/state   {"name": q}      → 当前态 + 结构版本 + 判据版本
② POST /api/quantum/qubit/route   {"name": q}      → decision / branch / allowed_edges / 候选
③ 分支（下表）
④ 只有 execute：生成 QWF → parse_qwf → run_ir → 轮询 → 取节点指标 → 换算 → 落演练证据
```

`route` 请求**不传 actions**：服务端会按当前态的所有合法边（`edges_for(state)`）生成候选，
这样「允许什么」只有服务端一个真源。

### 退出码

| 退出码 | 分支 | 含义 | 有没有发实验 |
|--------|------|------|--------------|
| `0` | execute / done | 继续了，或已到终态 | 发了（done 除外） |
| `2` | G0 reject | 当前态没有合法边，或动作越界 | **没发** |
| `3` | G3 ask_llm | 合法边存在，但模板库没有可用写法，需要人挑 | **没发** |
| `4` | G4 hold | 闸门拦截 / 写授权关闭 / 自环次数用尽 | **没发** |
| `5` | — | 服务不可用 | 没发 |

## 3. 为什么 G3 要停下

`route()` 在 G3 只回答「**允许**哪些动作」，不回答「**该做**哪个」。
真机上有几条隐性约束只有人知道：

- `sq.spectroscopy` 必须显式给 `freq` 频段，否则会退化成标量而崩
- `sq.t1` 必须固定 `zpa`，否则不是 1D 时间扫描
- 同一时刻扫哪一段、用多少点、要不要先看 S21 —— 这些是判断，不是规则

所以脚本在 G3 **停下并打印候选**，由驾驭层（人或 DSH 里的 agent）指定后带参数续跑：

```bash
python server/tools/measurement_loop.py --qubit q0                        # 停在 G3，exit 3
python server/tools/measurement_loop.py --qubit q0 --decide-fn sq.piamp   # 指定后继续
python server/tools/measurement_loop.py --qubit q0 --decide-fn sq.spectroscopy \
    --decide-params '{"freq": [4.35, 4.30, -0.002]}'
```

指定的动作**必须**出现在上一轮的候选里，否则脚本拒绝执行（同一退出码 3）——
这是「只用合法边内的动作」这条红线的机械保障。

状态存在服务端（`/qubit/state` 每次现算），所以**续跑不丢进度**，也不需要脚本自己存上下文。

## 4. 证据从哪来

一次 run 结束后，脚本从节点的 `result.metrics` 取指标，按固定口径换成状态机认的键：

| 运行指标 | 证据键 | 口径 |
|----------|--------|------|
| `contrast` | `contrast` | 直取 |
| `separation` | `contrast` | `separation ≤ 1` 直取，否则 `÷2` 归一化 |
| `SNR` | `contrast` | `÷20` 归一化（仅演示口径） |
| `T1` / `T2`（秒） | `t1_us` / `t2_star_us` | `×1e6` |
| `gate_fidelity` | `xeb_gate_fidelity` | 直取 |
| `f10` | `freq_locked` | `f10>0` 记真；模拟后端 `f10=0` 时按「扫频已执行完成」记，**note 里写明** |

**为什么走演练证据通道**：状态机的台账通道（`evidence_from_records`）只认「成功 **且** 有 `dataset_ids`」的记录；
模拟后端的 run 两者都不满足，所以循环写的是 `POST /qubit/evidence`（`source=loop`，带 TTL）。
控制台状态图会显示黄色「演练证据 N 条」标记 —— 看的人一眼知道这不是真机实测。

真实台账证据**优先级更高**：同一个键两边都有时，台账那份留着，演练那份被丢弃并记入 `conflicts`。

## 5. 常用命令

```bash
# 只看状态和下一步，不发实验
python server/tools/measurement_loop.py --qubit q0 --dry-run

# 清空演练证据后从头来
python server/tools/measurement_loop.py --qubit q0 --reset

# 最多走 8 轮，每轮间隔 15 秒（方便盯控制台）
python server/tools/measurement_loop.py --qubit q0 --max-rounds 8 --interval 15

# 缺真机必需参数就停下（默认只在提示里说，模拟后端不拦）
python server/tools/measurement_loop.py --qubit q0 --strict-params --decide-fn sq.spectroscopy
```

## 6. 已知现象（不是 bug）

- **模拟后端会跳档**：`sq.piamp` 的 `separation` 折算出 `contrast>0.7`，一轮就从 `freq_locked` 跳到
  `state_discriminated`，`amp_calibrated` 不出现。真机上这两步是分开的，判据与边表没变。
- **一轮可能什么都不产出**：某些实验在模拟后端只回一个非证据指标（如 `pi_amplitude`），
  换算后为空 → 不写证据 → 状态不动。脚本连续两轮没推进就停下（exit 3），不空转。
- **模板库目前是空的**（`server/config/state_machine.json` 的 `templates: []`），所以真实运行时
  **每轮都会落 G3**。等真机跑够样本、模板晋升后才会走 G1 自动执行 —— 这是刻意的「先有人把关」。

## 7. 和演示剧本的分工

| | `server/tools/demo_control_console.py` | `server/tools/measurement_loop.py` |
|---|---|---|
| 下一步做什么 | 六阶段写死 | 每轮问 `route()` |
| 状态机角色 | 只被问「现在到哪」 | 决定允许哪些动作 |
| 适用 | 组会展示控制台会动 | 真的往下标定 |
| 证据来源标记 | `source=manual` | `source=loop` |

两者都走 QWF、都写演练证据通道，区别只在「谁决定下一步」。

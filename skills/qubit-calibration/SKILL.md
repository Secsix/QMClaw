---
name: qubit-calibration
description: >
  单比特标定全流程，按状态转移图组织：每一步都是「当前状态 → 允许的实验 kind →
  产出什么证据 → 状态推进到哪」。覆盖六态主线、频率锁定与态区分的两条并列路径、
  脉冲质量（DRAG/保真度）与变频比特（zpa↔频率、T1 随 zpa）两个并列 scope。
  Triggers: '标定', 'calibration', '从头标定', '调到哪一步', '还缺什么', '下一步跑什么',
  '六态', 'state', 'characterized', 'spectroscopy', 'T1', 'T2', 'XEB', 'DRAG', 'zpa'
version: 2.0.0
author: qmclaw
---

# 单比特标定：跟着状态机走

**不要背 13 步线性流程。** 流程是图：当前状态决定允许哪些动作，动作产出证据，证据推状态。
图随芯片变化（有耦合器才有双比特图、变频比特才有频率空间图），所以**先问后端**：

```
GET  http://localhost:3002/api/quantum/qubit/scopes        # 这台芯片启用了哪几张图
POST http://localhost:3002/api/quantum/qubit/state        # {"name": "q0"} 当前在哪一步、缺什么
POST http://localhost:3002/api/quantum/qubit/route        # {"name": "q0"} 下一步允许的动作与判定
```

## 主线六态（`scope=qubit`）

| 状态 | 含义 | 产出它的典型实验 | 进入它需要的证据 |
| --- | --- | --- | --- |
| `unknown` | 什么都没有 | — | — |
| `freq_locked` | 找到比特频率 | 扫频（`spectroscopy`） | `freq_locked`（扫频成功、峰位可信） |
| `amp_calibrated` | π 脉冲幅度已定 | `piamp` / Rabi | `amp_calibrated` 或可用对比度 |
| `state_discriminated` | 能区分 \|0>/\|1> | `iqraw` | `contrast` 判据通过 |
| `coherence_measured` | 能量/相干时间已测 | `t1` + `ramsey*` | `t1_us` 与 `t2_star_us` 都过 |
| `characterized` | 单比特已表征 | `xeb` | 上面两项 + `xeb_gate_fidelity` 过 |

**两条并列路径（顺序不唯一）**：`freq_locked` 之后既可以先 `piamp` 再 `iqraw`，
也可以先 `iqraw` 再 `piamp`。谁先由 Router 按你已有的证据决定，不要为了"按流程"重跑已完成的实验。

**自环与回退**（图里就写着，别自己发明）：
- 自环：频率漂了重扫（rescan）、幅度漂了重扫 Rabi（resweep）、区分度掉了重采 IQ（recenter）；
- 回退：相干变差 → 回频率；区分度变差 → 回幅度；
- 自环有次数上限（`max_attempts`），用尽就被 Router 判 hold，交人。

## 并列 scope：脉冲质量（`scope=pulse`）

只有当芯片档案要求 DRAG 时启用（`drag_required`）。

| 状态 | 需要 | 典型实验 |
| --- | --- | --- |
| `pulse_unknown` | — | — |
| `drag_tuned` | `drag_delta` | `drag_calibrate`（可用 `allxy`、`pulse_shape` 做旁证） |
| `fidelity_verified` | `drag_delta` + `single_qubit_fidelity` | `xeb` |

前置：单比特至少要 `state_discriminated`。DRAG 没调就测保真度，测的是别的误差。

## 并列 scope：频率空间（`scope=tunable`）

只有变频比特启用（`tunable`）。可调比特的 T1、频率会随 zpa 变，
所以「一个 zpa 上的 T1」不能代表整颗比特。

| 状态 | 需要 | 典型实验 |
| --- | --- | --- |
| `tunable_unknown` | — | — |
| `zpa_mapped` | `zpa_curve`（点数够） | `spectroscopy_auto`（扫 zpa×频率） |
| `t1_mapped` | `zpa_curve` + `t1_by_zpa` | `t1` 在不同 zpa 下重复 |

## 每一步怎么做（统一四步）

1. `POST /api/quantum/qubit/route {"name": "q0"}` —— 看 `decision`：
   - `accept`：`chosen_action` 就是该跑的实验（含参数），直接执行；
   - `ask_llm`：`allowed_actions` 里挑一个（挑完把 `fn` 告诉循环脚本），**只能挑里面的**；
   - `reject`：缺证据，按 `missing` 补；`hold`：闸门/授权/次数用尽，交人。
2. 用语义端点执行：`POST /api/quantum/experiments/run {"kind": "...", "qubits": ["q0"], "params": {...}}`
   —— kind 与后端支持矩阵看 `GET /api/quantum/experiments/catalog`。
3. 拟合：`POST /api/analysis/fit {"dataset_id": "...", "kind": "..."}` 拿物理量，
   `metrics/convert` 拿证据键。
4. 写完证据后重新读状态：`POST /api/quantum/qubit/state`。**推进了就说推进到哪，没推进就说没推进**，
   不要把"跑了一次实验"当成"状态变了"。

## 门限（真源 `server/config/lab_criteria.json`）

| 指标 | 门限 | 说明 |
| --- | --- | --- |
| `contrast` | `<0.2` 不可用 / `0.2–0.4` 弱可用 / `>0.4` 合格 / `>0.7` 良好 | 态区分 |
| `t1_us` | `≥ 20` | 能量弛豫 |
| `t2_star_us` | `≥ 1` | Ramsey 相干 |
| `xeb_gate_fidelity` | `≥ 0.99` | 单比特门 |
| `single_qubit_fidelity` | `≥ 0.999`（脉冲质量图） | 更严的单比特指标 |
| `drag_delta` | 绝对值 `≤ 0.05` | DRAG 系数偏离 |
| `zpa_curve` / `t1_by_zpa` | 点数 `≥ 5` | 两张曲线图的最低采样 |

阈值是 `TODO: calibrate` 的那几项还没用真机校准过，读到边界值时要在结论里说明。

## 坑（现场踩出来的）

1. **别拿 discriminator 中心间距当区分度**：读出的中心每次都可能重算，看着"变了"不代表物理变了。
   判据看 `contrast`，不看中心。
2. **光谱图是平的，就别信它写回的频率**：平坦数据的拟合峰位是噪声；用 `pidf` 复核。
3. **对比度卡住先怀疑频率**，不是幅度。频率错了，怎么调 π 幅度都上不去。
4. **T1 需要 zpa 维度**才严谨；只有时间一维的数据要标注「未扫 zpa」。
5. **状态会过期**：每个状态有 TTL（如 `freq_locked` 1 天、`characterized` 30 天）。
   过期不是"数据没了"，是"不能再拿它当准入依据"，要重测。
6. **模拟后端会跳档**：qiskit/离线回放常常一步从 `unknown` 跨到 `state_discriminated`，
   演示时如实说明"这一步被模拟后端一步跨过"。

## 相关

- 状态机细节：`skills/qmclaw-state-graph/SKILL.md`
- 循环执行：`skills/qmclaw-measurement-loop/SKILL.md`
- 分析口径：`skills/qubit-data-analysis/SKILL.md`

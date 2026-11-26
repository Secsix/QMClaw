---
name: qmclaw-measurement-loop
description: >-
  测控状态驱动的标定循环：每轮现查比特状态、只在状态机允许的合法边内选动作、跑成 QWF、
  把本次 run 的指标换算成证据写回，直到 characterized 或需要人决定。
  触发词：标定 qN、跑一轮测控、从头标定、让状态机自己往下走、下一步该跑什么实验、
  measurement loop、state driven、状态驱动循环、calibration loop。
version: 1.0.0
author: QMClaw
---

# 测控循环（驾驭层操作规程）

驱动一个比特从 `unknown` 走到 `characterized`。**唯一入口**是本仓的循环脚本：

```bash
py -3.12 server/tools/measurement_loop.py --qubit <qN> [选项]
```

不要手写 `POST /qubit/state`，也不要自己拼实验代码——循环已经把这些串好了。
也不要照 `demo_control_console.py` 的六阶段清单跑：那是演示剧本，顺序写死，状态机不参与决策。

## 一、触发

用户说这类话时进来：「标定 q0」「从头到尾标定一下 qN」「跑一轮测控」「让它自己往下走」
「看看状态机认为下一步该干什么」。

## 二、开跑之前（每次都要做）

1. **三个服务在不在**：`:3002`（网关）、`:3003`（quantum）、`:3008`（workflow）。
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3002/api/workflow-runs
   ```
   不通就停下告诉用户。**不要自行启动服务**——本仓约定：服务启停由人控制。
2. **先读状态，别先跑实验**：
   ```bash
   py -3.12 server/tools/measurement_loop.py --qubit q0 --dry-run
   ```
   回答里带一句：「当前在 `<state>`，合法下一步有 N 个动作」。

## 三、每一轮怎么读

脚本每轮打印固定格式，汇报时**照抄这些行**，不要改写成叙事：

```
[轮 2/8] q0 当前 freq_locked
[路由] decision=ask_llm / branch=G3 · 模板库没有适用写法
[候选] · sq.piamp  <-  freq_locked->amp_calibrated
[运行] completed · 耗时 12s · 图像 1 张 · 指标 pi_amplitude
[换算] pi_amplitude -> contrast ...
[看板] freq_locked -> amp_calibrated
```

再补一句「这一轮凭什么变」：哪个指标、按什么口径换成哪个证据键（`[换算]` 行直接引）。

## 四、退出码 → 该干什么

| 退出码 | 含义 | 动作 |
|---|---|---|
| `0` | 推进了，或已到 `characterized` | 继续下一轮 / 收工汇报 |
| `2` | G0：当前态没有合法边，或动作越界 | **什么都没跑**。把 `[缺口]` 的 `missing.states` / `missing.edges` 翻译成人话，建议先补哪个实验 |
| `3` | G3：模板库没有可用写法，需要人挑 | **把候选原样给用户**，说明每个 fn 的 intent；真机参数（`sq.spectroscopy` 的 freq、`sq.t1` 的 zpa）缺就问值 |
| `4` | G4：闸门拦截 / 写授权关闭 / 自环次数用尽 | 停下并分清是哪一种：写授权没开（改配置+重启，由人做）、状态过期（重判）、自环用尽（换策略或问人） |
| `5` | 服务不可用 | 报告哪个端点不通，等用户处理 |

`3` 之后续跑（**状态在服务端，续跑不丢进度**）：

```bash
py -3.12 server/tools/measurement_loop.py --qubit q0 --decide-fn sq.piamp
py -3.12 server/tools/measurement_loop.py --qubit q0 --decide-fn sq.spectroscopy \
  --decide-params "{\"freq\": [4.35, 4.30, -0.002]}"
```

`--decide-fn` 必须出现在上一轮的 `[候选]` 里，不在里面脚本会直接拒绝。

## 五、硬规矩

1. **只用 `allowed_edges` 内的动作**。不在候选里的实验不做，哪怕它「看起来更该跑」。
2. **不改 `server/config/`**。`state_machine.json`（结构）与 `lab_criteria.json`（阈值）是真源，
   改它们等于改判据，必须由人决定。
3. **不动 `vendor/OpenQWF/`**（上游 submodule）；DSL 语义改动只在 `Secsix/OpenQWF` 做。
4. **写闸门保持默认关闭**。需要 `allow_execute` / `allow_set_params` 时停下让用户改配置并重启，
   不要用环境变量偷偷打开。
5. **证据只从本次 run 来**，不手写 `POST /qubit/evidence` 去「推」状态。脚本写的是
   `source=loop`（演练证据），看板上有黄色标记；不要说成台账实测。
6. **不进则停**：同一状态连续两轮没推进，脚本自己会停。先查指标键名是否被采纳，别硬跑。
7. **模拟后端会跳档**：`sq.piamp` 折算的 contrast 常 >0.7，`amp_calibrated` 会被一步跨过。
   如实报告「跳档」，不要伪造中间态。

## 六、收工

- 说清终态：`characterized`，还是停在哪一步、为什么停。
- 给证据链：哪几个证据键、各来自哪次 run（`[图像]` 有 plotPath）、有效期到什么时候。
- 提醒一句：`/control`（`:8081`）每 3 秒自轮询，用户可以自己看状态图、QWF 流程图和本次运行图。

## 七、和演示剧本的分工

| | `demo_control_console.py` | `measurement_loop.py` |
|---|---|---|
| 下一步做什么 | 代码里写死的六阶段 | 每轮问 `route()` |
| 状态机角色 | 只被问「现在到哪」 | 决定允许哪些动作 |
| 适用 | 组会展示「控制台会动」 | 真的往下标定 |
| 证据 | `source=manual` | `source=loop` |

两者都走 QWF、都写演练证据通道——因为模拟后端的 run 没有 `dataset_ids`，台账通道看不到它们。
真机跑出 `dataset_ids` 后，台账证据优先级高于演练证据，不需要改脚本。

## 八、相关

- 四张状态图（qubit / pair / pulse / tunable）与换芯片改法：`skills/qmclaw-state-graph/`
- 循环契约与人机边界：`docs/MEASUREMENT_LOOP.md`
- 控制台演示剧本：`docs/CONTROL_CONSOLE_DEMO.md`

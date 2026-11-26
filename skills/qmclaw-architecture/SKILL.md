---
name: qmclaw-architecture
description: >
  QMClaw 的整体架构：四层分工（驾驭层 / 机制层 / 执行层 / 证据层）、微服务边界、
  状态机与 QWF 的关系、技能与智能体各自该做什么。回答"这个功能该落在哪一层、
  为什么不能直接调底层"。取代旧的三份架构文档（决策架构 / 框架总览 / 工具调度）。
  Triggers: '架构', '分层', '哪一层', '谁是驱动者', '微服务', 'agent_service', 'hermes',
  'RuleEngine', '工具调度', 'framework', 'architecture', 'QMClaw 怎么跑'
version: 1.0.0
author: qmclaw
---

# QMClaw 架构：四层分工

## 一句话

**人给目标，智能体做判断，状态机做准入，QWF 做执行载体，台账做证据。**

四层是从「谁负责什么」切的，不是从「代码在哪个目录」切的。判断一个改动该落在哪层，
就看它改变的是目标、判断、准入/执行，还是证据。

## 四层

| 层 | 负责 | 不负责 | 落点 |
| --- | --- | --- | --- |
| 驾驭层 | 把人的意图翻成可执行目标；选择下一步动作；解释结果 | 判定状态、拼实验代码 | 会话（DSH / 未来的 agent_service）+ 技能文档 |
| 机制层 | 状态机、Router 准入判定、模板与成功率、闸门 | 执行实验、写硬件 | `services/common/{qubit_state,state_machine,router,templates}.py` + `config/state_machine.json` |
| 执行层 | 把动作落成 QWF 并跑起来（kind → 后端） | 决定跑什么 | `services/workflow_service`、`services/quantum_service` |
| 证据层 | 台账与演练证据、指标换算、状态记录 | 猜测、补值 | `data/run-records`、`data/state_ledger`、`services/common/metric_convert.py` |

**状态机在证据回流的路径上**：它读台账得出状态，不从对话上下文里读结论。
所以「上一次我说这比特是 characterized」不是证据；「台账里有一次成功的 T1 与 XEB」才是。

## 微服务边界

| 服务 | 端口 | 只做什么 |
| --- | --- | --- |
| quantum | 3003 | 实验语义层（kind → lqcs / qiskit / offline_sim）、比特状态、路由判定、证据库、模板库 |
| analysis | 3004 | 语义拟合（`/fit`）、指标换算（`/metrics/convert`）、离线复现（`/replay/*`） |
| workflow | 3008 | QWF 解析、IR 执行、run 台账 |
| hermes | 3012 | 技能目录扫描、记忆索引 |
| agent | 3005 | **预留的对话窗口**，当前不是驱动者 |

网关 `:3002` 转发 `/{section}/*` 到上表端口。技能与智能体只认网关路径，
不直连微服务端口，也不 import 任何底层测控库。

当前**驱动者是谁**：会话里的智能体（DSH）通过 `server/tools/measurement_loop.py` 或
`/experiments/run` 驱动；`agent_service` 是给以后接自有对话界面用的，现在没有接进决策链。

## 状态机与 QWF 的关系

- **状态机**回答「现在在哪、还缺什么、下一步允许做什么」（结构在 `config/state_machine.json`，
  阈值在 `config/lab_criteria.json`，芯片能力在 `config/device_capabilities.json`）。
- **QWF** 是「这次到底怎么跑」的唯一载体：一次实验、一次判断、一次断言都写成 QWF，
  可检查、可复现、可审计。
- 两者通过 **Router** 相连：Router 用状态 + 历史 + 闸门给出 `decision`（accept / ask_llm /
  reject / hold），只有 accept 的动作能直接进执行层；ask_llm 时 LLM 的候选也必须在
  `allowed_edges` 里，越界即拒绝。

## 五条硬规矩

1. **技能不写厂商代码**：不 import 底层库、不拼 `sq.xxx(...)`、不写绝对路径。
   要用哪个实验就问 `/experiments/catalog`，要拟合就问 `/fit`。
2. **准入唯一**：不要绕开 Router 直接跑实验来"试探"；试探也是动作，也要走判定。
3. **证据只从运行来**：`run_id` / `dataset_ids` 是凭据；没有凭据的数字不进状态。
4. **写闸门默认关闭**：真机写操作由人显式打开（配置或环境变量），模拟后端不受影响。
5. **错了就停**：同一状态连续两轮没有推进，停下来问人，不要换着法子重试。

## 常见误解

- ❌「状态机是给前端画的图」——图只是视图；状态机是准入判定，前端读它。
- ❌「智能体查状态是规定动作」——状态是 Router 的输入，智能体做判断时**必须**先读，
  但读不是形式，读到什么决定能不能做。
- ❌「先跑跑看，跑完再看状态」——没准入就跑，等于没有状态机。

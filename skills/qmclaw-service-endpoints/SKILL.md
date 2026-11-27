---
name: qmclaw-service-endpoints
description: >
  QMClaw 微服务端点清单（自动生成，勿手改）。查某个能力由哪个服务提供、走哪个网关路径、
  参数是什么。优先用语义端点（/experiments/*、/qubit/*、/fit），不要拼厂商代码。
  Triggers: '接口', 'endpoint', 'API', '哪个服务', '怎么调', '网关', '端口', 'service endpoints'
version: 2.0.0
author: qmclaw
---

# QMClaw 服务端点

> **本文件由 `server/tools/gen_service_endpoints.py` 生成**；改端点请改代码后重新生成，
> `--check` 在 CI 里拦住过期清单。

## 先看这张表：能力 → 该调哪个端点

技能、智能体、脚本**只应该调这些语义端点**。它们内部才碰厂商库：

| 想做什么 | 端点 | 说明 |
| --- | --- | --- |
| 有哪些实验 kind、哪些后端支持、参数 schema | `GET /api/quantum/experiments/catalog` | |
| 按 kind 跑实验（lqcs / qiskit / offline_sim 三后端统一） | `POST /api/quantum/experiments/run` | |
| 当前芯片启用了哪几张状态转移图 | `GET /api/quantum/qubit/scopes` | |
| 某比特/某个 scope 的当前状态、缺什么证据 | `POST /api/quantum/qubit/state` | |
| 状态机路由判定（G0–G4，纯计算不执行） | `POST /api/quantum/qubit/route` | |
| 模板库（G1/G2 的唯一开关） | `GET /api/quantum/qubit/templates` | |
| 按 kind 拟合数据集，产出物理量与证据 | `POST /api/analysis/fit` | |
| 能拟合哪些 kind | `GET /api/analysis/fit/kinds` | |
| 运行指标 → 状态机证据键的唯一换算 | `POST /api/analysis/metrics/convert` | |
| 离线复现：按 dataset_id 重新拟合并出图 | `POST /api/analysis/replay/plot` | |

其余端点（`/execute`、`/plot/offline/v2`、自由命令字符串…）是**排障入口**，
不是给技能用的：它们要求调用方会写厂商代码，换一台机器就失效。

## 服务与端口

| 服务 | 端口 | 网关前缀 | 端点数量 |
| --- | --- | --- | --- |
| agent | 3005 | `/api/agent` | 7 |
| analysis | 3004 | `/api/analysis` | 27 |
| hermes | 3012 | `/api/hermes` | 11 |
| image | 3007 | `/api/image` | 6 |
| llm | 3006 | `/api/llm` | 3 |
| quantum | 3003 | `/api/quantum` | 44 |
| qubitclient | 3010 | `（不对外）` | 10 |
| workflow | 3008 | `/api/workflow` | 19 |

`server/src/index.ts` 把 `:3002` 网关的 `/{section}/*` 转发到上表的服务端口；
技能里写 `http://localhost:3002/api/<section>/<path>`，不要直连微服务端口。

## agent · 智能体会话

网关前缀 `/api/agent`（直连 `:3005`）：

- `/health`
- `/chat`
- `/chat/stream`
- `/tasks`
- `/tasks/status`
- `/tools`
- `/memory`

## analysis · 分析（语义拟合、指标换算、离线复现）

网关前缀 `/api/analysis`（直连 `:3004`）：

- `/health`
- `/connect`
- `/switch_session`
- `/execute`
- `/plot`
- `/plot/historical`
- `/plot/experiments`
- `/stats`
- `/analyze`
- `/datasets`
- `/load`
- `/mode`
- `/datasets/offline`
- `/plot/offline`
- `/plot/offline/v2`
- `/plot/online/v2`
- `/fit`
- `/fit/kinds`
- `/metrics/convert`
- `/metrics/schema`
- `/health/report`
- `/replay/datasets`
- `/replay/plot`
- `/variants/types`
- `/variants/generate`
- `/variants/list`
- `/variants/plot`

## hermes · Hermes 技能与记忆

网关前缀 `/api/hermes`（直连 `:3012`）：

- `/ws`
- `/health`
- `/models`
- `/chat`
- `/sessions`
- `/memory`
- `/memory/add`
- `/memory/edit`
- `/memory/delete`
- `/skills`
- `/cron`

## image · 图像分类与分析

网关前缀 `/api/image`（直连 `:3007`）：

- `/health`
- `/classify/single`
- `/classify/folder`
- `/train`
- `/model/info`
- `/training/status`

## llm · 模型调用

网关前缀 `/api/llm`（直连 `:3006`）：

- `/chat`
- `/models`
- `/stats`

## quantum · 测控（量子实验执行、比特状态机、路由、证据）

网关前缀 `/api/quantum`（直连 `:3003`）：

- `/health`
- `/connect`
- `/status`
- `/qubits`
- `/experiments`
- `/execute`
- `/jobs`
- `/switch_session`
- `/sessions`
- `/session_tree`
- `/qubit/params`
- `/qubit/registry`
- `/qubit/set_params`
- `/qubit/apply_dataset`
- `/qubit/contrast`
- `/qubit/capabilities`
- `/qubit/probe_transition`
- `/qubit/runs`
- `/qubit/assess`
- `/qubit/state`
- `/qubit/state_table`
- `/experiments/catalog`
- `/experiments/run`
- `/qubit/scopes`
- `/qubit/evidence`
- `/qubit/evidence/reset`
- `/qubit/pair/state_table`
- `/qubit/pair/state`
- `/qubit/pair/route`
- `/qubit/route`
- `/qubit/templates`
- `/qubit/templates/outcome`
- `/qubit/templates/refresh`
- `/qubit/templates/retire`
- `/qubit/templates/reset`
- `/qubit/writer`
- `/qubit/profile`
- `/qubit/replay`
- `/mode`
- `/backends`
- `/datasets`
- `/qiskit/run`
- `/qiskit/experiments`
- `/qiskit/health`

## qubitclient · qubitclient

网关前缀 `/api/qubitclient`（直连 `:3010`）：

- `/health`
- `/reload`
- `/families`
- `/describe`
- `/classify`
- `/reasoning`
- `/assess_fit`
- `/extract_params`
- `/evaluate`
- `/analyze_full`

## workflow · QWF 工作流

网关前缀 `/api/workflow`（直连 `:3008`）：

- `/health`
- `/workflows`
- `/workflows/create`
- `/workflows/run`
- `/workflows/status`
- `/workflows/cancel`
- `/workflows/validate`
- `/workflows/run_ir`
- `/workflows/manifest`
- `/workflows/ir_from_canvas`
- `/workflows/parse_qwf`
- `/workflows/lint`
- `/workflows/qwf_examples`
- `/workflows/qwf_to_canvas`
- `/workflows/canvas_to_qwf`
- `/workflows/stmt_to_qwf`
- `/workflows/saved`
- `/runs`
- `/runs/delete`

## 约定

1. **只调语义端点**：`/experiments/run` 收 `kind`，`/fit` 收 `kind`；两者各自知道
   「这个实验在 qiskit 上做不了」和「这个 kind 该用什么模型」。
2. **结果要留凭据**：实验返回 `run_id` / `dataset_ids`，把它们带上再谈结论。
3. **证据只从运行来**：`/qubit/state` 的证据来自台账与演练库；不要把记忆里的数字直接写进去。
4. **写闸门**：真机写操作默认关闭（`fallback_config.json` 的 actuation），
   模拟后端（qiskit / offline_sim）不受影响。

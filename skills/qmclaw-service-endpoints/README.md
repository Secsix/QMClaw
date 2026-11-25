# qmclaw-service-endpoints — 微服务端点总表

> **铁律：凡涉及真机（LabRAD / 硬件 / 数据集 / 寄存器）的功能，一律通过这些微服务端点实现，
> 不要在沙箱进程里 `import lqms` 直连 LabRAD，也不要自己起第二个客户端连 Ray。**
> 直连会绕过写闸门、绕过任务账本、绕过 LabRAD 会话锁，还会和正式服务抢 Ray 集群。

技能文件：[`SKILL.md`](SKILL.md)（version 1.4.0）。AI 助手（Claude Code / Codex / Hermes）
在需要「跑实验、读写寄存器、切会话、画图拟合、进队列」时加载它。

## 它解决什么问题

凭记忆敲端点最容易错：这个表把 **端口 → 服务 → 路径 → 请求体 → 响应 → 用途** 一次列全，
并给出「什么场景必须走哪个端点」的判定口诀。

## 覆盖范围

| 端口 | 服务 | 职责 |
|------|------|------|
| 3002 | Express 网关 | 前端唯一入口，`/api/*` 代理到下面各服务 |
| 3003 | `quantum_service` | 真机执行：跑实验、读写寄存器、切会话 |
| 3004 | `analysis_service` | 绘图与拟合（`:3003` 不画图） |
| 3005 | `agent_service` | 对话、任务、工具、记忆 |
| 3006 | `llm_service` | LLM 推理代理 |
| 3007 | `image_service` | 实验图分类 |
| 3008 | `workflow_service` | DSL（`.qwf`）解析、lint、清单、执行 |
| 3009 | `task_queue` | 任务、票据、manifest、额度、lane |
| 3010 | `qubitclient_service` | VLM 看图（`/extract_params` `/describe`） |
| 3011 | `qca_service` | 量子校准分析 |
| 3012 | `hermes_service` | Hermes Agent：对话与技能路由 |

## 关键规则（摘录，细则见 SKILL.md）

- 涉及 **真机 / 硬件 / 寄存器 / 数据集 / qwf / 队列 / 绘图** 任一关键词 ⇒ 先调本技能再动手。
- 实验端点一律带 `do_plot=False`（服务进程内画图会卡住 quantum 端点）。
- 画指定数据集必须带 `dataset_index`。
- 写寄存器走 `:3003/qubit/set_params`，按数据集快照回写走 `:3003/qubit/apply_dataset`。
- 改 `server/services/workflow_service/dsl/` 门面后要重启 `:3008`。
- 真机功能实现规约见 SKILL.md §7。

## 触发词

微服务、端点、endpoint、API、端口、3003、3004、3008、3009、调实验接口、读写寄存器、
真机接口、怎么调用服务、跑实验、LabRAD 连接、register、任务队列、入队、票据、额度、
绘图、拟合、离线模式、写闸门、goal 块、require_task、热升级。

## 安装

```bash
# Claude Code
cp -R skills/qmclaw-service-endpoints ~/.claude/skills/
# Codex
cp -R skills/qmclaw-service-endpoints ~/.codex/skills/
```

平台侧是否已索引：`GET :3002/api/hermes/skills/debug`。

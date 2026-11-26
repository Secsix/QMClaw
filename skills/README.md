# QMClaw Skills

QMClaw 的技能包集合。每个技能是一个以 `SKILL.md` 为入口的指令包，供 AI Agent
（DeepSeek Harness / Claude Code / Codex 等）加载后按自然语言触发。

- 技能总数：**10 个技能包 + 1 个共享基础包**（`_shared`）
- 统一元数据：`SKILL.md` front-matter（`name` / `description` / `version` / `author`），
  `description` 里写明触发词，Agent 据此路由
- 安装方式是拷**整个目录**（含 `README.md`、`scripts/`、`assets/`），不要只拷 `SKILL.md`

---

## 一条边界（先看这个）

**技能只写「怎么判断、按什么顺序、门限是多少」，不写「怎么调用底层测控软件」。**

- 真机接口、厂商库、数据格式（hdf5 / registry / DataVault / 编解码）**全部在微服务里**；
  技能里出现这些名字，只允许停在「这些由服务处理」这一句。
- 技能要动手时，一律调语义端点（`/api/experiments/run`、`/api/analysis/fit`、
  `/api/quantum/qubit/state` …），端点清单一律看
  [`qmclaw-service-endpoints`](qmclaw-service-endpoints/)（该文件由
  `server/tools/gen_service_endpoints.py` 从路由表生成，请勿手改）。
- 这条边界由 `server/checks/_skills_boundary_check.py` 在 CI 里守；
  新增技能前先跑一次。

配套文档：[`docs/SERVICE_BOUNDARY.md`](../docs/SERVICE_BOUNDARY.md)（为什么这么切）。

---

## 安装

```bash
git clone https://github.com/Secsix/QMClaw.git
cd QMClaw

# Claude Code
mkdir -p ~/.claude/skills
cp -R skills/_shared skills/qmclaw-* skills/qubit-* skills/quantum-* skills/s21-* ~/.claude/skills/

# Codex
mkdir -p ~/.codex/skills
cp -R skills/_shared skills/qmclaw-* skills/qubit-* skills/quantum-* skills/s21-* ~/.codex/skills/
```

装完**重启 Agent 会话**才会被加载。其他 Agent 同理，拷到它自己的技能目录。

> DeepSeek Harness 可用项目级目录 `<repo>/.dsh/skills/`（本仓已就绪，
> 里面是指向 `skills/<name>/` 的目录联接），无需拷到用户目录。

更新：

```bash
git pull
cp -R skills/_shared skills/qmclaw-* skills/qubit-* skills/quantum-* skills/s21-* ~/.claude/skills/
```

> 平台本体（后端 / 前端）不需要装技能也能独立跑，见根目录
> [`README.md`](../README.md) 的「快速开始」。

---

## 技能索引

### 机制层：状态机与循环

| 技能 | 版本 | 用途 | 触发词 |
|------|------|------|--------|
| [`qmclaw-state-graph`](qmclaw-state-graph/) | 2.0 | 四张并列状态转移图（单比特 / CZ / 脉冲质量 / 频率空间）、边与闸门、`when` 表达式、按芯片能力启用、换芯片改法 | 状态转移图、状态机、scope、还缺什么证据、换芯片、TTL、闸门 |
| [`qmclaw-measurement-loop`](qmclaw-measurement-loop/) | 2.0 | 状态驱动的标定循环：每轮现查状态、只在合法边内选动作、跑成 QWF、证据回写 | 标定 qN、跑一轮测控、让状态机自己往下走、下一步跑什么 |

### 物理层：怎么测、怎么判

| 技能 | 版本 | 用途 | 触发词 |
|------|------|------|--------|
| [`qubit-calibration`](qubit-calibration/) | 2.0 | 单比特标定全流程（跟着状态机走）：六态主线、两条并列路径、脉冲质量与频率空间两个 scope、每步判据与现场坑 | 标定、calibration、从头标定、调到哪一步、下一步跑什么 |
| [`qubit-contrast-tuning`](qubit-contrast-tuning/) | 2.0 | 态区分度迭代调优：对比度卡住先怀疑频率、别用 discriminator 中心、平坦光谱不可信；含症状对照表与实测基线 | 对比度、contrast、区分度、分不清 0 和 1、SNR 低 |
| [`qubit-data-analysis`](qubit-data-analysis/) | 2.0 | 语义拟合与质量判定：`/analysis/fit` 按 kind 提物理量、`/metrics/convert` 换证据、门限与常见坑 | 数据分析、拟合、T1 怎么算、对比度、保真度 |
| [`qmclaw-offline-replay`](qmclaw-offline-replay/) | 2.0 | 离线复现：没有真机时用历史数据集重跑图与拟合，明确标注「离线 ≠ 现场」 | 离线、offline、历史数据、复现、replay、没连上真机 |
| [`quantum-experiment-plotter`](quantum-experiment-plotter/) | 1.1 | 一维 / 二维学术级绘图规范（坐标、单位、误差带、导出格式） | 绘图、可视化、plot、figure |
| [`s21-photo-reader`](s21-photo-reader/) | 2.0 | S21 "find dips" 截图读数（**仅人工辅助**）：读表 → 人核对 → 经受控写入端点回写 | S21 照片、读图、截图识别、比特号、波谷值 |

### 平台层：架构与端点

| 技能 | 版本 | 用途 | 触发词 |
|------|------|------|--------|
| [`qmclaw-architecture`](qmclaw-architecture/) | 2.0 | 四层架构（驾驭 / 机制 / 执行 / 证据）、微服务边界、工具面与写闸门 | QMClaw、架构、分层、微服务、边界、写闸门 |
| [`qmclaw-service-endpoints`](qmclaw-service-endpoints/) | 2.0 | 全部微服务端点清单（端口 / 路径 / 请求体 / 用途），**由路由表自动生成** | 微服务、端点、API、端口、怎么调服务 |
| [`_shared`](_shared/) | — | 公共术语表、量子参数定义、质量标准（被上面技能引用） | — |

> 已下线的旧技能（`qmclaw-framework-overview`、`qmclaw-decision-architecture`、
> `qmclaw-tool-scheduling`、`qmclaw-tuneup-workflow`、`lqms-offline-datavault-plotting`）：
> 前三个已被状态机 + Router 取代，合并进 `qmclaw-architecture`；
> `qmclaw-tuneup-workflow` 的线性 15 步与状态图冲突；
> `lqms-offline-datavault-plotting` 的职责已收进 `analysis_service`（`/replay/*`）。

---

## 目录约定

```
skills/
├── _shared/                 # 公共上下文：术语、参数、质量阈值（与其它技能一起装）
├── <skill-name>/
│   ├── SKILL.md             # 入口：front-matter + 触发词 + 工作流说明
│   ├── README.md            # 给人看的说明
│   ├── static/ assets/ scripts/   # 可选：静态资源、脚本
└── README.md                # 本文件：索引与安装
```

新增技能见 [`AGENTS.md`](../AGENTS.md) 的约定与
[`docs/REPO_LAYOUT.md`](../docs/REPO_LAYOUT.md) 的放位规则。

---

## 与平台的关系

技能描述**怎么判断与决策**，平台（`server/` + `web/`）提供**实际执行**能力：

```
AI Agent ──加载技能──> 自然语言意图
                          │
                          ▼
        Express 网关 :3002 ──代理──> Python 微服务 :3003–:3012
                          │                 │
                          │                 ├─ quantum_service  语义实验层 / 状态机 / 三后端
                          │                 ├─ analysis_service 语义拟合 / 离线复现 / 绘图
                          │                 ├─ workflow_service QWF DSL 编排与执行
                          │                 └─ agent_service    推理 + 长期记忆
                          ▼
                 Next.js 控制台 :8081
```

技能里提到「跑 `iqraw`」「拟合 `t1`」这类动作，实际落点就是
`POST /api/experiments/run {kind:"iqraw"}`、`POST /api/analysis/fit {kind:"t1"}`。

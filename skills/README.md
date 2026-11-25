# QMClaw Skills

QMClaw 的技能包集合。每个技能是一个以 `SKILL.md` 为入口的指令包，供 AI Agent
（Claude Code / Codex 等）加载后按自然语言触发；多数技能还附带脚本、参考文档与
示例资产，供 Agent 直接调用。

- 技能总数：12 个（1 个共享基础包 + 11 个技能包）
- 统一元数据：`SKILL.md` front-matter（`name` / `description` / `version` / `author`），
  `description` 里写明触发词，Agent 据此路由
- 安装方式见下方「安装」；装的是**整个目录**（含 `README.md`、`static/`、`scripts/`、
  `assets/`），不要只拷 `SKILL.md`

---

## 安装

```bash
git clone https://github.com/Secsix/QMClaw.git
cd QMClaw

# Claude Code
mkdir -p ~/.claude/skills
cp -R skills/_shared skills/qmclaw-* skills/qubit-* skills/quantum-* skills/lqms-* ~/.claude/skills/

# Codex
mkdir -p ~/.codex/skills
cp -R skills/_shared skills/qmclaw-* skills/qubit-* skills/quantum-* skills/lqms-* ~/.codex/skills/
```

装完**重启 Agent 会话**才会被加载。其他 Agent 同理，拷到它自己的技能目录。

更新：

```bash
git pull
cp -R skills/_shared skills/qmclaw-* skills/qubit-* skills/quantum-* skills/lqms-* ~/.claude/skills/
```

> 平台本体（后端 / 前端）不需要装技能也能独立跑，见根目录
> [`README.md`](../README.md) 的「快速开始」。

---

## 技能索引

| 技能 | 版本 | 用途 | 触发词 |
|------|------|------|--------|
| [`qmclaw-framework-overview`](qmclaw-framework-overview/) | 1.0 | 框架整体介绍：六层执行栈、三层决策、多智能体模型 | QMClaw、框架介绍、架构、六层栈 |
| [`qmclaw-decision-architecture`](qmclaw-decision-architecture/) | 1.0 | L1 RuleEngine / L2 学习规则 / L3 LLM 兜底三层决策 | 决策、三层决策、RuleEngine、规则引擎 |
| [`qmclaw-tuneup-workflow`](qmclaw-tuneup-workflow/) | 1.0 | 15 步单比特调校流程、状态机、Panel 批量校准 | 调校、工作流、tune-up、状态机 |
| [`qmclaw-tool-scheduling`](qmclaw-tool-scheduling/) | 1.0 | 工具白名单、MCP 接入、集中式调度与后端适配 | 工具调度、后端接口、MCP、Backend Adapter |
| [`qmclaw-service-endpoints`](qmclaw-service-endpoints/) | 1.4 | 全部微服务端点清单（端口 / 路径 / 请求体 / 用途） | 微服务、端点、API、端口、怎么调服务 |
| [`qubit-data-analysis`](qubit-data-analysis/) | 1.0 | T1 / Rabi 拟合、XEB 保真度、质量指标判定 | 数据分析、fitData、保真度、T1 |
| [`qubit-calibration`](qubit-calibration/) | 1.0 | 单比特完整标定序列与每步判定标准 | 单比特标定、qubit calibration、比特校准 |
| [`qubit-contrast-tuning`](qubit-contrast-tuning/) | 1.0 | 读出对比度（contrast）迭代调优：iqraw→piamp→pidf 闭环 | 态区分、对比度、contrast、f10 偏了 |
| [`quantum-experiment-plotter`](quantum-experiment-plotter/) | 1.0 | 一维 / 二维学术级绘图与数据拟合 | 绘图、可视化、plot、figure |
| [`s21-photo-reader`](s21-photo-reader/) | 1.0 | 从 S21 "find dips" 截图提取比特号→波谷值表格 | S21 照片、读图、截图识别、比特号 |
| [`lqms-offline-datavault-plotting`](lqms-offline-datavault-plotting/) | 1.0 | 不连 LabRAD 读历史 DataVault hdf5 复现实验图并导出矢量图 | 离线出图、DataVault、历史数据出图 |
| [`_shared`](_shared/) | — | 公共术语表、量子参数定义、质量标准（被上面技能引用） | — |

---

## 目录约定

```
skills/
├── _shared/                 # 公共上下文：术语、参数、质量阈值（必须与其它技能一起装）
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

技能描述**怎么用**量子测控，平台（`server/` + `web/`）提供**实际执行**能力：

```
AI Agent ──加载技能──> 自然语言意图
                          │
                          ▼
        Express 网关 :3002 ──代理──> Python 微服务 :3003–:3012
                          │                 │
                          │                 ├─ quantum_service  真机 / qiskit / 离线回放
                          │                 ├─ analysis_service 绘图、拟合
                          │                 ├─ workflow_service QWF DSL 编排
                          │                 └─ agent_service    推理 + 长期记忆
                          ▼
                 Next.js 控制台 :8081
```

技能里如果提到「调用 `:3003/execute`」「跑 `qwf_run.py`」这类动作，对应的就是上面这条链路。

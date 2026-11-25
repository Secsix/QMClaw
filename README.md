# QMClaw

可扩展的通用量子比特测控平台与技能框架 —— 平台负责**执行**（真机 / 模拟 / 离线），
技能包负责**教 AI Agent 怎么用**。

A scalable general-purpose framework for quantum measurement and control: an
execution platform (gateway + microservice + web console) plus a set of reusable
instruction bundles centred on `SKILL.md`.

---

<p align="center">
  <a href="https://github.com/Secsix/QMClaw/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
  </a>
  <a href="https://github.com/Secsix/QMClaw/stargazers">
    <img src="https://img.shields.io/github/stars/Secsix/QMClaw" alt="Stars">
  </a>
  <a href="https://github.com/Secsix/QMClaw/actions/workflows/checks.yml">
    <img src="https://github.com/Secsix/QMClaw/actions/workflows/checks.yml/badge.svg" alt="checks">
  </a>
  <a href="https://github.com/Secsix/QMClaw/blob/main/.github/workflows/checks.yml">
    <img src="https://img.shields.io/badge/CI-offline%20checks-4c9aff.svg" alt="CI">
  </a>
</p>

---

## 📦 里面有什么

| 组成 | 位置 | 说明 |
|------|------|------|
| 执行平台 | `server/` | Express 网关 :3002 + Python 微服务 :3003–:3012（真机 / qiskit / 离线三种后端） |
| 控制台 | `web/` | Next.js 14 仪表盘 :8081（工作流画布、Agent 对话、队列、绘图） |
| 技能包 | `skills/` | 12 个以 `SKILL.md` 为入口的 Agent 技能 → [索引](skills/README.md) |
| 领域脚本 | `qwf_scripts/` `qwf_examples/` | QWF DSL 实验与工作流脚本（校准、Ramsey、T1、XEB…） |
| 自检 | `checks/` | 17 项离线静态自检 + 3 项需真机/服务的在线自检 |
| 工具 | `tools/` | OpenQWF 同步与漂移检查、qiskit 冒烟、对比度调参、S21 读图、自动标定 |
| 文档 | `docs/` | 架构、运维、真机、DSL、上游同步等（见 [文档索引](#-文档索引)） |
| 上游 submodule | `vendor/` | OpenQWF（DSL 运行时）、hermes-agent、QubitClient、QCA Blueprint |

---

## 🚀 快速开始

### 前置条件

| 组件 | 版本 | 必需？ |
|------|------|--------|
| Node.js | ≥ 18 | 是（网关 + 前端） |
| Python | ≥ 3.10 | 是（微服务 / QWF 工具） |
| Git | 任意 | 是 |
| LabRAD | — | 否，**只有接真机时需要** |

没有真机也能完整跑通：量子后端默认按 `lqcs → qiskit → offline_sim` 的顺序自动降级
（qiskit 本地模拟 / 历史数据回放），详见 [`docs/DEMO_NO_LQCS.md`](docs/DEMO_NO_LQCS.md)。

### 方式 A：只装技能（用 AI Agent，不跑平台）

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

重启 Agent 会话后直接用自然语言触发（"执行单比特校准工作流"、"怎么画 T1 拟合图"）。
完整说明见 [`skills/README.md`](skills/README.md) 与 [`install.md`](install.md)。

### 方式 B：跑起平台（推荐）

```bash
# 1) 取代码（含上游 submodule；OpenQWF 是私有仓，没凭证时可跳过，只影响 QWF 功能）
git clone --recurse-submodules https://github.com/Secsix/QMClaw.git
cd QMClaw

# 2) 后端依赖
cd server
npm install                                    # 网关
pip install -r python/requirements.txt          # Python 微服务
pip install -r python/requirements-qiskit.txt   # 可选：无真机时用 qiskit 模拟
cd ..

# 3) 配置（密钥只走 .env，不进版本库）
cp server/.env.example server/.env              # 填 MINIMAX_API_KEY / OPENAI_API_KEY 等
# 机器相关的标定值放 server/config/*.local.json（同样不入库）
```

启动（**两个终端**，服务启停由人控制）：

```bash
# 终端 1：Python 微服务
cd server
python scripts/start_all_services.py --all     # :3003 quantum / :3004 analysis / :3005 agent ...

# 终端 2：Express 网关
cd server
npm run dev                                    # :3002

# 终端 3：前端控制台
cd web
npm install
npm run dev                                    # :8081
```

打开 **http://localhost:8081** —— 前端默认连 `http://localhost:3002`（Express 网关），
用 `NEXT_PUBLIC_API_URL` 可改。

验证：

```bash
python checks/run_all_checks.py                # 17 项离线静态自检
curl http://localhost:3002/health              # 网关健康
curl http://localhost:3003/health              # 量子服务健康（含当前后端）
```

### 方式 C：接真机（LabRAD）

1. 装 LabRAD 侧依赖并让 `labrad` 可导入：`pip install -r server/python/requirements.txt`
2. 按实验室约定配置 `server/config/session.json`（DataVault session）
3. 标定值写 `server/config/device_profile.json`（**不入库**，仓内有
   `device_profile.example.json` 作模板）
4. 真机写闸门默认关闭：需要下发实验时由人显式打开
   `server/config/fallback_config.json` 里的 `allow_execute` / `allow_set_params`
5. 细节与故障排查见 [`docs/REAL_MACHINE.md`](docs/REAL_MACHINE.md)

> **安全约定**：写闸门只在服务端强制，打开必须由人显式执行并记录；
> 执行超时会标记 `stuck` 并拒绝后续下发。

---

## 🧩 常用命令

| 目的 | 命令 |
|------|------|
| 起全部微服务 | `python server/scripts/start_all_services.py --all` |
| 起网关（开发） | `cd server && npm run dev` |
| 起前端 | `cd web && npm run dev` |
| 离线自检（17 项） | `python checks/run_all_checks.py` |
| 单跑一个自检 | `python checks/_p3_check.py` |
| 在线自检（需服务/真机） | `python checks/_p3_e2e.py` · `_registry_ep_check.py` · `_apply_dataset_ep_check.py` |
| 生成 DSL 实验脚本 | `python qwf_gen.py --help` |
| 运行 DSL 工作流 | `python qwf_run.py --help` |
| 校验 DSL 脚本 | `python qwf_lint.py qwf_examples/*.qwf` |
| 对比度调参工具 | `python tools/iqraw_contrast_tune.py --help` |
| 上游 OpenQWF 漂移检查 | `python tools/check_openqwf_drift.py` |

---

## 🏗️ 架构

### 部署拓扑

```
┌──────────────────────────────────────────────────────────────┐
│  浏览器 → Next.js 控制台 :8081                              │
└──────────────────────────────────────────────────────────────┘
                              │ HTTP / REST + WebSocket
┌──────────────────────────────────────────────────────────────┐
│  Express 网关 :3002（TypeScript + Socket.IO）                │
└──────────────────────────────────────────────────────────────┘
                              │ 代理
┌──────────────────────────────────────────────────────────────┐
│  Python 微服务                                               │
│  quantum :3003   analysis :3004   agent :3005   llm :3006    │
│  image :3007      workflow :3008  task_queue :3009           │
│  qubitclient :3010  qca :3011   hermes :3012                 │
└──────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   LabRAD 真机          qiskit Aer 模拟        历史数据离线回放
```

量子后端是一条**有序策略**（`server/config/fallback_config.json` →
`quantum_service.backends.order`，默认 `["lqcs", "qiskit", "offline_sim"]`）：
装了真机的机器行为不变；没装 lqcs 的机器自动落到 qiskit；再不行就回放历史数据。

### Six-Layer Execution Stack

```
┌─────────────────────────────────────────────────────────────┐
│  L6: Natural Language Interface    │  User commands         │
├─────────────────────────────────────────────────────────────┤
│  L5: Analysis & Report             │  Data fitting          │
├─────────────────────────────────────────────────────────────┤
│  L4: Workflow Orchestration        │  Workflow execution    │
├─────────────────────────────────────────────────────────────┤
│  L3: Tool Scheduling               │  Tool dispatch         │
├─────────────────────────────────────────────────────────────┤
│  L2: Knowledge & Rules             │  RAG, rule learning    │
├─────────────────────────────────────────────────────────────┤
│  L1: Hardware Interface            │  Instruments/simulators│
└─────────────────────────────────────────────────────────────┘
```

### Three-Layer Decision Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  L3: LLM Fallback Layer                                    │
│  ├── Use case: New problems, no rule match                 │
│  ├── Speed: ~300ms                                         │
│  └── Cost: API cost                                        │
├─────────────────────────────────────────────────────────────┤
│  L2: Learned Rules Layer                                   │
│  ├── Source: L3 success case promotion                     │
│  ├── Speed: <1ms                                           │
│  └── Cost: $0                                              │
├─────────────────────────────────────────────────────────────┤
│  L1: RuleEngine Layer                                      │
│  ├── Content: Expert knowledge, physics rules              │
│  ├── Speed: <1μs                                           │
│  └── Cost: $0                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 目录结构

```
QMClaw/
├── server/            # 后端：Express 网关 :3002 + Python 微服务 :3003–3012
├── web/               # 前端：Next.js 控制台 :8081
├── skills/            # Agent 技能包（12 个）→ skills/README.md
├── checks/            # 自检脚本 + run_all_checks.py（17 离线 / 3 在线）
├── tools/             # 运维与科研工具（OpenQWF 同步、qiskit 冒烟、调参、读图）
├── qwf_scripts/       # QWF DSL 实验与工作流脚本（校准、Ramsey、T1…）
├── qwf_examples/      # QWF DSL 示例（受 lint 保护，CI 必过）
├── experiments/       # 实验产物与报告
├── docs/              # 架构 / 运维 / 真机 / DSL / 上游同步文档
├── vendor/            # 上游 submodule（OpenQWF、hermes-agent、QubitClient、QCA）
├── qwf_gen.py         # QWF DSL CLI：gen / run / lint（薄壳委派上游 OpenQWF）
├── AGENTS.md          # 开发约定（本仓唯一约定文件）
├── install.md         # 详细安装指南
└── requirements.txt   # Python 依赖
```

各目录职责与新增文件的落位规则见 [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md)。

**不入库的东西**（本机保留，clone 下来不会有）：

- `legacy/` —— 历史目录（`new_ctrl` / `new_templates` / `measure_scripts`），仅供历史脚本参考
- `server/config/*.local.json`、`server/config/device_profile.json` —— 本机覆盖配置与真机标定值
- `server/config/memory/` —— Agent 长期记忆（`episodes/`、`skills/`、`index.json`）
- `server/data/`、`logs/`、`cache/`、`qwf_out/` 等运行产物
- 一次性诊断脚本与**设计过程文档**（`_local/docs/*PLAN*.md` 等）—— 见 `.gitignore` 的私有文档规则段

---

## 📄 文档索引

| 文档 | 内容 |
|------|------|
| [`install.md`](install.md) | 详细安装指南（技能安装、Python 包用法、MCP 配置、故障排查） |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 平台架构与模块边界 |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | 日常运维：起停、配置、自检清单、排障 |
| [`docs/REAL_MACHINE.md`](docs/REAL_MACHINE.md) | 真机接入、写闸门、只读回归自检 |
| [`docs/DEMO_NO_LQCS.md`](docs/DEMO_NO_LQCS.md) | 无真机环境下的 qiskit / 离线演示 |
| [`docs/QWF_NODES.md`](docs/QWF_NODES.md) | QWF DSL 节点参考 |
| [`docs/qiskit_backend.md`](docs/qiskit_backend.md) | qiskit/Aer 本地模拟后端配置与用法 |
| [`docs/RUNTIME_DEPS.md`](docs/RUNTIME_DEPS.md) | 运行时依赖与安装校验 |
| [`docs/OPENQWF_SYNC.md`](docs/OPENQWF_SYNC.md) | 上游 OpenQWF 同步规则（门面 / vendor 副本） |
| [`docs/ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md) | 后端适配器接入指南 |
| [`docs/CONTROL_CONSOLE_DEMO.md`](docs/CONTROL_CONSOLE_DEMO.md) | 控制台演示脚本 |
| [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) | 仓库结构与文件放位规则 |
| [`docs/QMClaw介绍及架构.md`](docs/QMClaw介绍及架构.md) | 中文版整体介绍 |
| [`docs/QMClaw_Skills_用户手册.md`](docs/QMClaw_Skills_用户手册.md) | 技能包使用手册 |
| [`AGENTS.md`](AGENTS.md) | 开发约定（提交节奏、日志、实验约定） |
| [`skills/README.md`](skills/README.md) | 技能索引与安装 |

---

## 🧩 技能包

12 个技能覆盖框架理解、决策架构、调校流程、工具调度、微服务端点、数据分析、
标定、对比度调优、学术绘图、S21 读图、离线 DataVault 出图。

索引与触发词见 **[`skills/README.md`](skills/README.md)**。

---

## 🛠️ Roadmap

- [ ] 多智能体协作 —— 支持多个 agent 并行执行不同任务
- [ ] 工作流可视化增强 —— 前端展示 agent 执行步骤流程图
- [ ] 语音输入 / 输出 —— 语音命令与语音播报
- [x] Agent 流式对话与长期记忆（`/api/agent/chat/stream`、`server/config/memory/`）
- [x] 三层决策架构 + 工具白名单 + MCP 接入
- [x] 真机 / qiskit / 离线三档后端策略

---

## 📄 Paper

If QMClaw is useful for your research, please cite our paper:

**[QMClaw: A Scalable General-purpose Framework for Quantum Measurement and Control](https://arxiv.org/pdf/2609.04674)**

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

新增技能 / 新增文件放位规则见 [`AGENTS.md`](AGENTS.md) 与
[`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md)。提交前请跑一遍
`python checks/run_all_checks.py`。

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- QMClaw framework developed by the QMClaw team
- Inspired by [nature-skills](https://github.com/Yuan1z0825/nature-skills)
- Built for the quantum computing community

---

## 📧 Contact

- **GitHub Issues**: [Issues](https://github.com/Secsix/QMClaw/issues)

---

<p align="center">
  Made with ❤️ for the quantum computing community
</p>

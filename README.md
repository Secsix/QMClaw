# QMClaw

可扩展的通用量子比特测控平台与技能框架。

A scalable general-purpose framework for quantum measurement and control, providing reusable instruction bundles centered on `SKILL.md`.

---

<p align="center">
  <a href="https://github.com/Secsix/QMClaw/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
  </a>
  <a href="https://github.com/Secsix/QMClaw/stargazers">
    <img src="https://img.shields.io/github/stars/Secsix/QMClaw" alt="Stars">
  </a>
  <a href="https://github.com/Secsix/QMClaw/releases">
    <img src="https://img.shields.io/github/v/release/Secsix/QMClaw" alt="Version">
  </a>
</p>

---

## 🌟 Features

| Feature | Description |
|---------|-------------|
| **QMClaw Framework Overview** | 六层执行栈、三层决策架构、多智能体模型 |
| **Three-Layer Decision** | L1 RuleEngine / L2 Learned Rules / L3 LLM Fallback |
| **Single-Qubit Calibration** | 15 步标准校准流程：S21 → Spectroscopy → Rabi → T1 |
| **Tool Scheduling** | 工具白名单、MCP 协议、集中式调度与分布式执行 |
| **Data Analysis** | T1/Rabi 拟合、XEB/RB 保真度、报告生成 |
| **Quality Assessment** | SNR、Visibility、T1、保真度等质量指标体系 |
| **Experiment Plotter** | 学术级可视化，支持 Nature/IEEE/APS/Springer 样式 |

---

## 📋 TODO

### High Priority
- [ ] **SSE 流式处理支持** - Agent 对话支持 Server-Sent Events 流式传输，实现实时进度更新
  - 状态：当前使用轮询模式，需要解决 Python 后台线程 stdout 通信问题
  - 相关文件：`job_runner.py`、`index.ts`、`AgentChatPanel.tsx`

### Medium Priority
- [ ] **Agent 记忆持久化** - 将 agent 对话历史存储到数据库，支持跨会话上下文
- [ ] **多智能体协作** - 支持多个 agent 并行执行不同任务
- [ ] **工作流可视化** - 在前端展示 agent 执行步骤的流程图

### Nice to Have
- [ ] **语音输入/输出** - 支持语音命令和语音播报结果
- [ ] **自动化测试套件** - 为 agent 能力编写集成测试

---

## 📦 Skill Index

| Skill | Version | Purpose | Trigger Keywords |
|-------|---------|---------|------------------|
| [QMClaw框架概述](./skills/qmclaw-framework-overview/README.md) | v1.0 | 框架架构、六层栈、三层决策、多智能体 | "QMClaw", "框架介绍", "架构" |
| [QMClaw三层决策架构](./skills/qmclaw-decision-architecture/README.md) | v1.0 | L1/L2/L3 决策层、RuleEngine、规则引擎 | "决策", "RuleEngine", "规则" |
| [QMClaw单比特调校工作流](./skills/qmclaw-tuneup-workflow/README.md) | v1.0 | 15 步校准流程、状态机、Panel 批量校准 | "校准", "调校", "calibration", "tune-up" |
| [QMClaw工具调度与后端接口](./skills/qmclaw-tool-scheduling/README.md) | v1.0 | 工具白名单、MCP、Center Agent、Backend Adapter | "工具调度", "后端", "tool scheduling" |
| [QMClaw数据分析与质量评估](./skills/qubit-data-analysis/README.md) | v1.0 | T1/Rabi 拟合、XEB 保真度、质量指标 | "数据分析", "保真度", "T1", "quality" |
| [量子比特单比特校准标定](./skills/qubit-calibration/README.md) | v1.0 | 完整校准标定流程、参数管理、质量判定 | "校准", "标定", "单比特", "qubit" |
| [量子实验绘图与分析](./skills/quantum-experiment-plotter/README.md) | v1.0 | 一维/二维绘图、学术图表、数据拟合 | "绘图", "可视化", "plot", "figure" |
| [离线DataVault数据出图](./skills/lqms-offline-datavault-plotting/README.md) | v1.0 | 无LabRAD环境读hdf5重建DataLab、批量出图、矢量导出 | "离线出图", "DataVault", "历史数据出图", "datavault" |

---

## 🚀 Quick Start

### 1. Installation

#### AI Agent 客户端（Claude Code / Codex 等）

```bash
# Clone the repository
git clone https://github.com/Secsix/QMClaw.git
cd QMClaw

# Install skills
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

#### Manual Installation

Copy the `skills/` directory to your agent's skill directory:

```bash
# For Claude Code
~/.claude/skills/

# For Codex
~/.codex/skills/

# For other agents
<your-agent-skill-dir>/
```

### 2. Usage

After installation, invoke skills naturally:

```
Use the QMClaw framework overview skill to introduce the architecture.
执行单比特校准工作流。
如何进行 T1 拟合和 XEB 保真度计算？
```

### 3. Example: Single-Qubit Calibration

```python
import sys
# Add your workflow directory path here
WORKFLOW_DIR = '/path/to/your/sq_workflow'  # Linux/Mac
# WORKFLOW_DIR = r'D:\path\to\sq_workflow'  # Windows
sys.path.insert(0, WORKFLOW_DIR)

import labrad
from lqms.pyle.workflow import switchSession
import sq
import numpy as np

# Connect to quantum control system
cxn = labrad.connect()
s = switchSession(cxn, user='YOUR_USER')
qobj = s.YOUR_QUBIT

# Execute 15-step calibration workflow
print("[1/15] S21 scan...")
sq.s21(qobj, update=False)

print("[4/15] Spectroscopy...")
sq.spectroscopy(qobj, freq=np.arange(2.7, 2.9, 0.001))

# ... continue with remaining steps
print("[15/15] T1 measurement...")
sq.t1(qobj, zpa=0)
```

> **Note:** Adjust `WORKFLOW_DIR` and connection parameters according to your lab's setup.

---

## 📁 Project Structure

仓库是「平台 + 技能」一体：根目录是完整工程（后端 / 前端 / 工具 / 文档 / 技能）。

```
QMClaw/
├── server/            # 后端：Express :3002 + Python 微服务
├── web/               # 前端：Next.js :8081
├── checks/            # 自检脚本 + run_all_checks.py（17 项离线清单）
├── experiments/       # 实验脚本与产物（自动化单比特标定 / s21_image / reports / t1_fit）
├── docs/              # 设计与运维文档
├── tools/             # 运维工具（OpenQWF 同步/漂移、比特体检、照片读取、自动标定）
├── skills/            # Agent 技能（SKILL.md）
├── qwf_scripts/       # QWF DSL 脚本与工作流
├── qwf_examples/      # QWF DSL 示例
├── vendor/            # 上游仓 submodule（OpenQWF / hermes-agent / QubitClient ...）
├── qwf_gen.py         # QWF DSL CLI：gen / run / lint（薄壳委派上游 OpenQWF）
├── AGENTS.md          # 开发约定（本仓唯一约定文件）
├── install.md         # 安装指南
├── requirements.txt   # Python 依赖
└── pyproject.toml     # Python 包配置
```

`start-qmclaw.ps1` / `deploy-local.ps1` / `mcp_tools_new.py` 是本机脚本（gitignored），
不在仓库里。

- `legacy/`（`new_ctrl` / `new_templates` / `measure_scripts` 等历史目录）本机保留但**不入库**，
  仅供历史脚本与文档参考。
- `server/config/*.local.json` 为本机覆盖配置，同样**不入库**。
- 各目录职责与新增文件的落位规则见 [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md)。

### skills/ 目录清单

| Skill | 说明 |
|-------|------|
| `_shared/` | 通用术语、量子参数、质量标准 |
| `qmclaw-framework-overview/` | 框架架构、六层栈、三层决策 |
| `qmclaw-decision-architecture/` | L1 / L2 / L3 决策层 |
| `qmclaw-tuneup-workflow/` | 15 步校准流程 |
| `qmclaw-tool-scheduling/` | 工具白名单、MCP、调度与后端适配 |
| `qmclaw-service-endpoints/` | 各微服务接口清单 |
| `qubit-data-analysis/` | T1 / Rabi 拟合、XEB 保真度、质量指标 |
| `qubit-calibration/` | 单比特校准标定 |
| `qubit-contrast-tuning/` | 对比度调优 |
| `quantum-experiment-plotter/` | 学术级绘图与数据拟合 |
| `lqms-offline-datavault-plotting/` | 离线 DataVault 批量出图 |
| `s21-photo-reader/` | S21 照片读取与拟合 |

## 🏗️ Architecture

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

## 📄 Paper

If QMClaw is useful for your research, please cite our paper:

**[QMClaw: A Scalable General-purpose Framework for Quantum Measurement and Control](https://arxiv.org/pdf/2609.04674)**

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [README](./README.md) | This file - project overview |
| [install.md](./install.md) | Detailed installation guide |
| [skills/*/README.md](skills/) | Individual skill documentation |
| [SKILL.md](./skills/*/SKILL.md) | Skill specifications |

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Adding a New Skill

See [AGENTS.md](./AGENTS.md)（开发约定）与 [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md)（文件放位规则）.

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
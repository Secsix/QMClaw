# Installation & Usage Guide

---

## 1. Environment Requirements

| Requirement | Version | Note |
|-------------|---------|------|
| Git | any | For cloning the repository |
| An AI Agent | Claude Code / Codex / other | For skill-based interaction (Option A) |
| Python | 3.10+ | Only for running the platform (Option B) |
| Node.js | ≥ 18 | Only for running the platform (Option B) |
| Docker & Docker Compose | — | Optional, for self-hosted MCP server |
| LabRAD + `lqcs` | — | Optional, only for real-hardware control |

**OS**: Windows / macOS / Linux

---

## 2. Installation

### 2.1 Clone the Repository

```bash
git clone https://github.com/Secsix/QMClaw.git
cd QMClaw
```

### 2.2 Option A — Install as Skills (No Python packaging needed)

QMClaw Skills are reusable instruction bundles centred on `SKILL.md`.
Copy the whole folder, not only `SKILL.md`, because many skills depend on `_shared/`, scripts, or README context.

#### Claude Code

**Manual Local-Skill Installation:**

```bash
mkdir -p ~/.claude/skills
cp -R skills/_shared ~/.claude/skills/
cp -R skills/qmclaw-* ~/.claude/skills/
cp -R skills/qubit-* ~/.claude/skills/
cp -R skills/quantum-* ~/.claude/skills/
```

**Update after pulling new changes:**

```bash
git pull
cp -R skills/_shared ~/.claude/skills/
cp -R skills/qmclaw-* ~/.claude/skills/
cp -R skills/qubit-* ~/.claude/skills/
cp -R skills/quantum-* ~/.claude/skills/
```

Restart your AI agent (Claude Code / Codex) so newly added skills are picked up.

#### Codex

**Manual Local-Skill Installation:**

```bash
mkdir -p ~/.codex/skills
cp -R skills/_shared ~/.codex/skills/
for d in skills/qmclaw-* skills/quantum-*; do
  cp -R "$d" ~/.codex/skills/
done
```

Restart Codex after installation.

#### Other Agents or Manual Use

Copy the whole `skills/` directory into your prompt library or project.
Preserve `SKILL.md`, `README.md`, `static/`, scripts, assets, and `skills/_shared/` together.

### 2.3 Option B — Run the Platform (Backend + Frontend)

The platform is a Node.js gateway (`:3002`) plus a set of Python microservices
(`:3003`–`:3012`) with a Next.js console (`:8081`).

```bash
# ① Backend
cd server
npm install
pip install -r python/requirements.txt
cp .env.example .env          # fill in the LLM API key; measurement can run without it
python scripts/start_all_services.py --all
npm run dev                   # Express gateway on :3002
```

```bash
# ② Frontend (another terminal)
cd web
npm install
npm run dev                   # Next.js console on :8081
```

Then open <http://localhost:8081>.

Optional extras:

```bash
# qiskit simulator backend
pip install -r server/python/requirements-qiskit.txt

# demo mode without a real machine
pip install -r server/python/requirements-demo.txt
```

Without LabRAD the platform still starts: the quantum backend falls back in the
order `lqcs -> qiskit -> offline_sim` (see `server/config/fallback_config.json`).

> **Note**: this repository does **not** ship a pip-installable Python package.
> The legacy laboratory workflow package (`new_ctrl` / `new_templates`) lives in
> each lab's own `sq_workflow` directory, not in this repo. Platform Python
> dependencies are declared in `server/python/requirements.txt`.

---

## 3. Configuration

### 3.1 MCP Configuration (`.mcp.json`)

If you want to use the MCP protocol-based experiment tools, create or edit `.mcp.json`:

```json
{
    "mcpServers": {
        "quantum-service": {
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-http",
                "http://127.0.0.1:8008/mcp"
            ]
        }
    }
}
```

Adjust `http://127.0.0.1:8008/mcp` to point to your MCP server.

### 3.2 Quantum Control System Connection

If your skills need to connect to a quantum control system:

```python
import sys
# Add your workflow directory path here
WORKFLOW_DIR = r"/path/to/your/sq_workflow"  # Linux/Mac
# WORKFLOW_DIR = r"D:\path\to\sq_workflow"  # Windows
sys.path.insert(0, WORKFLOW_DIR)

import labrad
from lqms.pyle.workflow import switchSession
import sq

# Connect
cxn = labrad.connect(host='localhost', port=7682)
s = switchSession(cxn, user='YOUR_USERNAME')
qobj = s.YOUR_QUBIT_NAME  # e.g., 'q11ld4'
```

> **Note:** The quantum control system setup varies by lab. Consult your system administrator for the correct connection parameters and workflow directory path.

---

## 4. Usage

### 4.1 In AI Agents (Natural Language)

After installation, invoke skills naturally:

**Claude Code:**
```
执行单比特校准工作流。
如何进行 T1 拟合和 XEB 保真度计算？
QMClaw 的决策架构是什么？
```

**Codex:**
```
Introduce the QMClaw framework architecture
Execute the single-qubit calibration workflow
Calculate T1 fitting and XEB fidelity
```

### 4.2 In Python (Programmatic Access)

平台启动后，网关（`:3002`）就是稳定的程序化入口。下面这些请求在**任何后端**
（真机 / qiskit / 离线回放）下都可用：

```python
import json
import urllib.request

GATEWAY = "http://localhost:3002"

def get(path):
    with urllib.request.urlopen(GATEWAY + path, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ① 当前启用的量子后端（lqcs / qiskit / offline_sim）
print(get("/api/quantum/backends"))

# ② 可用比特列表
print(get("/api/quantum/qubits"))

# ③ 历史数据集
print(get("/api/quantum/datasets"))

# ④ 当前是 online / offline / auto 模式
print(get("/api/quantum/mode"))
```

要下发实验、跑工作流、读写寄存器，请看技能包
[`qmclaw-service-endpoints`](../skills/qmclaw-service-endpoints/SKILL.md)：
那里逐条列了 `:3003` / `:3004` / `:3008` 的请求体、响应结构与写闸门约束。

> **注意**：`new_ctrl` / `new_templates`（实验室侧工作流包）**不在本仓库**，
> 存放在各实验室自己的 `sq_workflow` 目录里，需按 §3.2 的方式自行接入。

### 4.3 Using the MCP Server Directly (Without AI Agent)

MCP 服务器入口随实验室部署方式而变（`swiftmcp` / docker-compose / 本机脚本）。
本仓库不提供可直接运行的 MCP server 脚本；接入后在 `.mcp.json` 里指向它即可：

```json
{
  "mcpServers": {
    "quantum-service": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-http", "http://127.0.0.1:8008/mcp"]
    }
  }
}
```

参数读写、实验下发等能力同样可以走 §4.2 的 HTTP 路径实现，不依赖 MCP。

---

## 5. Skill Index

完整索引（含版本、目录约定与安装方式）见 **[`skills/README.md`](skills/README.md)**。速查：

| Skill | Purpose | Trigger Keywords |
|-------|---------|------------------|
| `qmclaw-framework-overview` | Framework architecture, six-layer stack | "QMClaw", "框架介绍", "架构" |
| `qmclaw-decision-architecture` | L1/L2/L3 decision layers, RuleEngine | "决策", "RuleEngine", "规则" |
| `qmclaw-tuneup-workflow` | 15-step calibration, state machine, Panel batch | "校准", "调校", "calibration", "tune-up" |
| `qmclaw-tool-scheduling` | Tool whitelist, MCP, Center Agent, Backend Adapter | "工具调度", "后端", "tool scheduling" |
| `qmclaw-service-endpoints` | All microservice endpoints (port / path / request body) | "微服务", "端点", "API", "端口" |
| `qubit-data-analysis` | T1/Rabi fitting, XEB/RB fidelity, quality metrics | "数据分析", "保真度", "T1", "quality" |
| `qubit-calibration` | Complete single-qubit calibration and labeling | "校准", "标定", "单比特", "qubit" |
| `qubit-contrast-tuning` | Readout contrast tuning loop (iqraw -> piamp -> pidf) | "对比度", "contrast", "f10 偏了" |
| `quantum-experiment-plotter` | 1D/2D plotting, academic figures, data fitting | "绘图", "可视化", "plot", "figure" |
| `s21-photo-reader` | Extract qubit id -> valley value table from S21 screenshots | "S21 照片", "读图", "截图识别" |
| `lqms-offline-datavault-plotting` | Offline DataVault HDF5 -> lqms DataLab -> batch figures, vector export | "离线出图", "DataVault", "历史数据出图" |

---

## 6. Verify Installation

### 6.1 Verify Skills

After installation, restart your AI agent and verify the skills are loaded:

```
QMClaw 的框架架构是什么？
```

If the skill responds correctly, the installation is successful.

### 6.2 Verify Python Package

```bash
# ① 仓库级自检（无需真机，17 项离线检查）
python checks/run_all_checks.py

# ② 实验室工作流包冒烟测试（需要 legacy/new_ctrl，缺失时会提示 SKIP）
python server/tests/test_smoke.py
```

Expected output:
```
# ① 仓库级自检
... 16/17 OK（唯一 ERR 是被忽略目录 be2_work/ 的历史文件，与基线一致）

# ② 实验室工作流包冒烟测试
  RUN  test_import_new_ctrl
  ✅ PASS  test_import_new_ctrl
  ...
  ==================================================
    结果: 7 passed, 0 failed, 7 total
  ==================================================
```

若本机没有 `legacy/new_ctrl`，第 ② 步会直接提示 `SKIP` 并正常退出。

### 6.3 Verify MCP Server (Optional)

If you have started the MCP server (via `swiftmcp` or docker-compose), verify it is reachable:

```bash
curl -s http://127.0.0.1:8008/mcp | head -c 100
```

---

## 7. Troubleshooting

### Skills not appearing in the agent

1. Check that `SKILL.md` is directly inside the skill folder, not in a subdirectory.
2. Ensure `skills/_shared/` is copied alongside the skill folders.
3. Restart the agent session after installation.
4. Verify the folder names match exactly (no extra prefixes).

### `ModuleNotFoundError: No module named 'new_ctrl'`

`new_ctrl` / `new_templates` are **laboratory-side workflow packages and are not
part of this repository** (they usually sit in a lab's `sq_workflow` directory,
or in the local `legacy/` folder). Point Python at them:

```bash
# Option 1: legacy/ inside this repo
export PYTHONPATH="$(pwd)/legacy:$PYTHONPATH"

# Option 2: your own laboratory workflow directory
export PYTHONPATH="/path/to/sq_workflow:$PYTHONPATH"
```

The same applies to `pip install -e ".[mcp]"` / `".[plot]"` below: those extras
only pull dependencies, they do not install a `qmclaw` module.

### `ModuleNotFoundError: No module named 'swiftmcp'`

The MCP protocol support package is not installed. Choose one:

```bash
# Option 1: Install the [mcp] extra
pip install -e ".[mcp]"

# Option 2: Install swiftmcp directly
pip install swiftmcp

# Option 3: Use the official MCP Python SDK instead
pip install mcp>=1.0
```

### `ModuleNotFoundError: No module named 'matplotlib'`

The plotting support package is not installed. Choose one:

```bash
# Option 1: Install the [plot] extra
pip install -e ".[plot]"

# Option 2: Install matplotlib directly
pip install matplotlib
```

### Chinese text display issues

If Chinese characters appear as boxes, ensure your terminal and editor support UTF-8 encoding:

```bash
# Set UTF-8 encoding
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
```

### MCP server connection issues

If the MCP server is not reachable:

```bash
# Check if the server is running
curl -s http://127.0.0.1:8008/mcp

# If not running, start it
python mcp_tools_new.py  # 本机脚本（gitignored，不随仓库分发）；或 docker-compose up -d
```

---

## 8. Updating

To update QMClaw Skills to the latest version:

```bash
cd QMClaw
git pull

# Re-install skills
cp -R skills/_shared ~/.claude/skills/
cp -R skills/qmclaw-* ~/.claude/skills/
cp -R skills/qubit-* ~/.claude/skills/
cp -R skills/quantum-* ~/.claude/skills/

# Re-install Python package (if used)
pip install -e ".[full]"
```

Restart your AI agent (Claude Code / Codex) after updating.

---

## 9. Uninstalling

### Remove Skills

```bash
# Remove all QMClaw skills from Claude Code
rm -rf ~/.claude/skills/qmclaw-*
rm -rf ~/.claude/skills/quantum-*
# Optionally remove shared content
rm -rf ~/.claude/skills/_shared
```

### Remove Python Package

```bash
pip uninstall qmclaw
```

---

## 10. Support

- **GitHub Issues**: [https://github.com/Secsix/QMClaw/issues](https://github.com/Secsix/QMClaw/issues)
- **Documentation**: See `skills/*/README.md` for individual skill documentation
- **Contributing**: See `AGENTS.md`（开发约定）与 `docs/REPO_LAYOUT.md` for guidelines on adding new skills

---

> **Tip**: QMClaw Skills include a Mock implementation in `new_ctrl/task.py`, which allows you to test and demo the full workflow without actual quantum hardware.

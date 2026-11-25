# docs/ — 文档索引

本目录只放**随版本走、对外有用**的文档：怎么装、怎么跑、怎么接自己的测控系统、架构是什么。
**过程性文档（设计稿、待办、评审稿、合并方案）不入库**，只留在本机 `docs/` 里，
由 `.gitignore` 的「私有设计文档」规则段拦截。

## 目录

| 文档 | 内容 | 适合谁看 |
|------|------|----------|
| [`REPO_LAYOUT.md`](REPO_LAYOUT.md) | 仓库根目录结构与各目录职责 | 所有人（先看这个） |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 六层执行栈 + 三层决策架构 | 开发者 |
| [`QMClaw介绍及架构.md`](QMClaw介绍及架构.md) | 项目介绍与架构（中文速览） | 新人 / 汇报材料 |
| [`RUNTIME_DEPS.md`](RUNTIME_DEPS.md) | 运行时依赖清单与接线指南 | 部署者 |
| [`OPERATIONS.md`](OPERATIONS.md) | 日常运维手册：跑实验、重启服务、排障、自检脚本清单 | 值班 / 运维 |
| [`REAL_MACHINE.md`](REAL_MACHINE.md) | 真机接通（只读联调）与写闸门约定 | 真机操作者 |
| [`ADAPTER_GUIDE.md`](ADAPTER_GUIDE.md) | 测控系统适配指南：接入自己的后端 | 二次开发者 |
| [`qiskit_backend.md`](qiskit_backend.md) | qiskit/Aer 本地模拟后端 | 无真机环境 |
| [`DEMO_NO_LQCS.md`](DEMO_NO_LQCS.md) | 没有 lqcs 真机时怎么跑（qiskit 演示模式） | 演示 / 会议机器 |
| [`CONTROL_CONSOLE_DEMO.md`](CONTROL_CONSOLE_DEMO.md) | `/control` 控制台实测演示步骤 | 演示者 |
| [`QWF_NODES.md`](QWF_NODES.md) | `.qwf` 节点类型实测覆盖情况 | 写 DSL 的人 |
| [`OPENQWF_SYNC.md`](OPENQWF_SYNC.md) | OpenQWF（DSL 真源仓）引用与门面规则 | 改 DSL 的人 |
| [`QMClaw_Skills_用户手册.md`](QMClaw_Skills_用户手册.md) | 技能包使用手册 | 用 AI 助手的人 |

## 约定

- **新增文档先判断归属**：讲「怎么用 / 怎么接」的进本目录；讲「这次怎么做的 / 下一步做什么」的留在本机，不入库。
- 文档里引用仓库路径一律用**仓内相对路径**（`<仓库根>/server/...`），不要写本机绝对路径——
  `checks/_docs_paths_check.py` 会校验文档中的路径在仓内真实存在。
- 文档里提到的自检脚本必须在 `checks/run_all_checks.py` 的 `OFFLINE` / `ONLINE` 清单里，
  且在 `OPERATIONS.md` §4 表格中有一行（`_docs_paths_check.py` 双向校验）。

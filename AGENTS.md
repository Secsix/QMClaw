# AGENTS.md — QmClaw 开发约定

> 本文件是本仓**唯一**的开发约定文件（原 `CLAUDE.md` 已并入此处）。
> 开发进展写入 `.Codex/progress.md`，本文件只保留经用户确认的约定。

## 目录

- [配置文件](#配置文件)
- [DSL 独立仓：OpenQWF](#dsl-独立仓openqwf)
- [离线模式](#离线模式)
- [实验后端策略](#实验后端策略lqcs--qiskit--离线回放)
- [记忆数据](#记忆数据不纳入版本控制)
- [真机写闸门](#真机写闸门安全约定)
- [测控运行与驾驭层](#测控运行与驾驭层)
- [导师上游融合记录](#导师上游融合记录)
- [仓库卫生约定](#仓库卫生约定)
- [本机目录布局](#本机目录布局)
- [约定](#约定)
- [Compaction 压缩规则](#compaction-压缩规则自动压缩时生效)

## 配置文件

所有配置统一保存在 `server/config/` 目录：

| 文件 | 说明 |
|------|------|
| `session.json` | DataVault session 配置 |
| `experiment_configs.json` | 测控实验配置（函数、绘图命令） |
| `model_configs.json` | LLM 模型配置 |
| `fallback_config.json` | 离线模式配置（offline_mode 配置） |

**API Key 说明**：从环境变量读取，不保存在配置文件中
- `OPENAI_API_KEY`、`MINIMAX_API_KEY`、`ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`

**本机覆盖配置（不入库）**：被跟踪的 `config/*.json` 只放通用默认值（不含实验室账号与本机绝对路径）；
本机差异写同目录的 `config/*.local.json`（`session.local.json` / `system.local.json`），
查找顺序 `*.local.json` → 被跟踪 `*.json` → 代码默认值（`system.local.json` 深合并）。
这两个文件已被 `.gitignore` 挡，换机器时按被跟踪文件的结构复制一份改值即可。

**`system.json` 里有两个 `qiskit` 块，不是一个东西**（别再混）：

- `backends.qiskit` —— `scripts/job_runner.py` 的后端**插件槽位**，`QiskitBackend` 类
  **并不存在**，`enabled` 必须保持 `false`，改成 `true` 会让 job_runner 找不到类而失败
- 顶层 `qiskit` —— `quantum_service` 的 qiskit/Aer 本地模拟后端配置
  （`python_executable` / `shots` / `timeout_s` 等），见 `docs/qiskit_backend.md`

qiskit 是**可选依赖**（`server/python/requirements-qiskit.txt`，不在主 `requirements.txt` 里）：
没装不影响服务启动，只是 `/qiskit/run` 返回明确的 `qiskit_unavailable`。

## DSL 独立仓：OpenQWF（✅ **已融合** 2026-11-19）

DSL 的**实现**住在私有独立仓 `Secsix/OpenQWF`（`qwf/` 核心 + `adapters/` 适配器）；
本仓以 **git submodule** 引用它（`vendor/OpenQWF`）**——本仓不保存任何源码副本**，
只记录一个指针（gitlink → commit），外加一层兼容门面
`server/services/workflow_service/dsl/`。

| 项 | 值 |
|-----|-----|
| 上游仓 | `https://github.com/Secsix/OpenQWF.git`（**私有**，靠 git credential helper / `OPENQWF_TOKEN` 拉） |
| 上游分支 | `main` |
| **引用方式** | **submodule**：`vendor/OpenQWF`（见 `.gitmodules`）→ 本仓只存指针 `160000` |
| 首次拉取 | `git submodule update --init --recursive vendor/OpenQWF`（或 `python tools/sync_openqwf.py --init`） |
| 门面 | `dsl/*.py` 全部是薄壳：`sys.modules[__name__] = upstream("qwf.<mod>")` |
| 桥接 | `dsl/_upstream.py`：挂 `sys.path` + 注入 `QWF_CONFIG_DIR`/`QWF_VOCAB_PATH`/`QWF_DATA_DIR` |
| 更新指针 | `python tools/sync_openqwf.py --update`（= `git submodule update --remote`；**提交由人做**） |
| 看状态 | `python tools/sync_openqwf.py --status` / `--check`（离线自检：指针 == 本地 HEAD 且工作区干净） |
| 查漂移 | `python tools/check_openqwf_drift.py`（指针是否落后上游 `main`；CI 任务 `openqwf-drift`） |
| 细则 | **`docs/OPENQWF_SYNC.md`**（门面规则 / 路径表 / qiskit 转发红线 / 故障排查） |

**红线（本仓不存上游源码）**：本仓索引里 `vendor/OpenQWF` 只能有一个 `160000` 条目；
任何 `vendor/OpenQWF/**` 的普通文件都不该出现在本仓（`server/tests/test_openqwf_drift.py`
会红）。改上游代码 → 在 `vendor/OpenQWF/` 里提交并 push 到 `Secsix/OpenQWF` →
回本仓 `git add vendor/OpenQWF` 更新指针。

**两条不许越界的红线**（改这个模块时必须守住）：

1. **qiskit 只转发，不复刻** —— 上游 `adapters/qiskit.py` 走 HTTP 打到本仓
   `quantum_service`（:3003 `/qiskit/*`）；实现在本仓，上游不复制第二份。
   `config/system.json` 的 `backends.qiskit.enabled` 仍必须保持 `false`。
2. **门面里不许有 DSL 语义实现** —— 改 DSL 一律改上游仓，再同步 vendor；
   本仓只改门面（薄壳）。`server/tests/test_openqwf_drift.py` 会盯着这条。

**根级 QWF CLI**：`qwf_gen.py` / `qwf_run.py` / `qwf_lint.py` 是薄壳，委派上游
`vendor/OpenQWF/tools/` 同名脚本（补丁层只做三件事：budget 恒写 `max_datasets`、
`get()` 非 2xx 也读 body、audit/RunRecord 目录指向本仓 `server/data/`）；
批量实验脚本在 `tools/qwf_exp.py` / `tools/qwf_sweep.py`。详见 `tools/README.md`。

## 离线模式

quantum_service 和 analysis_service 支持离线模式，可在 LabRAD 不可用时使用历史数据：

- **配置**：`fallback_config.json` 中的 `offline_mode` 字段
- **模式切换**：POST `/mode` 接口，参数 `{"mode": "online"|"offline"|"auto"}`
  - `online`: 强制使用 LabRAD
  - `offline`: 强制使用离线数据
  - `auto`: 自动切换（LabRAD 断开时自动离线）
- **离线数据**：存放在 `server/data/offline_data/` 目录
- **索引生成**：运行 `python -m services.common.offline_data_provider --generate-index`

## 实验后端策略（lqcs / qiskit / 离线回放）

「用不用真机」是**一份有序策略**，不是布尔开关。唯一配置在
`fallback_config.json` → `quantum_service.backends.order`，默认
`["lqcs", "qiskit", "offline_sim"]`：

- `lqcs` 真机 → `qiskit` Aer 模拟 → `offline_sim` 历史回放
- **没有部署 lqcs 的机器自动落到 qiskit**（演示/开发用），真机机器行为不变
- `mode`(online/offline/auto) 现在只是这份策略的**兼容别名**：
  `online`→`["lqcs"]`、`offline`→`["offline_sim"]`、`auto`→配置 `order`
- 运行期偏好 `POST /backends {"prefer":"qiskit"}` **只对 `auto` 生效**
- 状态报告：`GET /backends`（网关 `/api/quantum/backends`）、`/health` 的 `backends` 字段

**三条不许越界的红线**（改这个模块时必须守住）：

1. `mode=online` 连不上真机时**只报错**，不许拿模拟数据冒充实测
2. 真机掉线时 `/qubits` 返回空 + 原因，**不返回演示比特**（演示比特只在 qiskit 生效时给）
3. 任一后端的探测/执行异常都要**就地转成「不可用 + 原因」**，不许冒泡影响别的服务

演示比特在 `config/demo_qubits.json`（q0–q4），演示机依赖清单在
`server/python/requirements-demo.txt`，完整演示手册见 `docs/DEMO_NO_LQCS.md`。

## 记忆数据（不纳入版本控制）

agent 的长期记忆存放在 `server/config/memory/`，**整个目录都在 `.gitignore` 中**：

| 路径 | 内容 |
|------|------|
| `episodes/*.json` | 每轮对话的完整记录（task / steps / status / metrics / response） |
| `skills/*.json` | 从对话里沉淀出的可复用技能 |
| `index.json` | 上面两者的摘要索引，每轮对话都会被重写 |

- **只存在于本地**：迁移机器或交付时必须**手工拷贝整个 `config/memory/` 目录**。
  漏拷会产生"索引里有记录、实体文件已丢失"的孤儿状态，`GET /memory/stats` 的 `orphanCount` 会暴露它
- **不要放进 git**：`index.json` 每轮对话都变，且单独存在没有意义 —— 它引用的是未跟踪的实体文件，
  clone 下来只会得到一份悬空目录

## 真机写闸门（安全约定）

**写闸门默认关闭**：`fallback_config.json` 的 `quantum_service.actuation` / `analysis_service.actuation`
里 `allow_execute` 与 `allow_set_params` 默认均为 `false`。

- **任何打开写权限的操作必须由人显式执行并记录**（改配置或设 `QMCLAW_ALLOW_EXECUTE` / `QMCLAW_ALLOW_SET_PARAMS`，然后重启服务）
- 闸门只在**服务端**强制；agent 侧只是提前解释
- 执行超时后服务标记 `stuck` 并拒绝后续下发，须人工确认现场安全后再 `reset_stuck` 或重启
- 提权步骤与故障排查见 `docs/REAL_MACHINE.md`

## 测控运行与驾驭层

标定一个比特只有一条入口：`server/tools/measurement_loop.py` —— 每轮先问 `/qubit/state`，再问 `/qubit/route`，
**只在 `allowed_edges` 内选动作**；G3（模板库没有可用写法）**必须停下由人指定** `--decide-fn`，不做自动选择。
操作规程见 DSH skill `qmclaw-measurement-loop`，契约与退出码见 `docs/MEASUREMENT_LOOP.md`。

- 任何实验都必须先落成 QWF 再提交（执行层唯一入口），不直接拼 `sq.xxx(...)` 代码
- 证据只从本次 run 的指标来，不手写证据推状态；模拟后端的演练证据标 `source=loop`，汇报时不得说成台账实测
- `server/config/state_machine.json`（结构）、`lab_criteria.json`（判据阈值）、写闸门三样一律由人改，agent 不改
- 底层测控细节只在微服务里：技能只写「怎么判断、按什么顺序、门限多少」，不写厂商库调用、数据格式、绝对路径；
  新增能力先问「服务端点在哪」，边界与落点表见 `docs/SERVICE_BOUNDARY.md`，由 `checks/_skills_boundary_check.py` 守
- 状态图随芯片变化：有哪些 scope（单比特 / 双比特 CZ / 脉冲质量 / 频率空间）由 `server/config/device_capabilities.json`
  的能力标签推导，前端只画启用的那几张。判断"某张图该不该在"先查 `GET /qubit/scopes`，别在前端写死。
  三层配置（base → chip → runtime）与换芯片改法见 `docs/STATE_GRAPH_PROFILES.md`

## 导师上游融合记录

导师上游 `Secsix/QMClaw`（main `03d838d`）已合入 `feat/c-real-machine`：

- 合并提交 `bf725e9`（父1 = 我方 `3d646c9`，父2 = 上游 `03d838d`），冲突 4 文件 / 6 处已按方案解掉
- 回退点：`git reset --hard backup/pre-upstream-merge-20261008-202936`
- 方案全文与 23 项验证清单见 `_local/docs/UPSTREAM_MERGE_PLAN.md`、待办见 `_local/docs/BACKLOG.md`
  （**均为私有文档，不在版本库**，只在本机保留）

## 仓库卫生约定

GitHub 上只保留**精简版**：可复用的代码、对外可用/运维类文档、通用配置。
设计文档、计划、评审记录、过程产物、一次性中间脚本**一律只留在本地**。

| 类别 | 例子 | 去向 |
|------|------|------|
| 对外可用文档 | `docs/OPERATIONS.md`、`docs/ARCHITECTURE.md`、`README.md` | 入库 |
| 私有设计文档 | `_local/docs/*DESIGN*.md`、`_local/docs/*PLAN*.md`、`_local/docs/TODO_*.md`、`_local/docs/BACKLOG.md` | 只留本地（`.gitignore` 已登记） |
| 本机配置 | `server/config/*.local.json`、`server/config/device_profile.json`、`server/config/memory/` | 只留本地 |
| 过程产物 | `_local/artifacts/`（自检归档等）、`*.log`、`experiments/reports/`、真机实测数据 | 只留本地 |
| 临时脚本 | 根级 `_tmp_*.py` / `_c*_edit*.py` 之类批量编辑脚本 | 用完即删，不入库 |

**红线**：

1. **个人机器路径不入库**：文档与代码里不写死 `C:\Users\<你>` / `D:\<本机目录>`；
   用相对路径、`Path(__file__)` 推导或环境变量。实验室机约定的 `D:\qmclaw\` 属于团队约定，可保留。
2. **批量改文件用脚本 + 一次性提交**：改完先跑 `python checks/run_all_checks.py`，
   再把临时脚本删掉，**不要把临时脚本提交进库**。
3. **自检是红线**：`checks/_repo_hygiene_check.py` 会在提交前拦住
   「临时产物入库 / 大文件入库 / 私文档入库 / 个人路径泄露」四类污染；
   新增自检脚本必须同时改三处：`checks/run_all_checks.py` 的 OFFLINE 清单、
   `docs/OPERATIONS.md` §4 表格、`checks/README.md`（`_docs_paths_check.py` 双向校验）。
4. **出库用 `git rm --cached`**：先 `git rm --cached <path>` 再登记 `.gitignore` 规则，
   磁盘文件保留；确实需要入库时用 `git add -f` 显式覆盖。

## 本机目录布局

仓库根既是代码仓根，也是本机运行时根；本机专属内容统一收进 `_local/`（整个目录不入库）。

| 目录 | 内容 | 入库 |
|------|------|------|
| `_local/docs/` | 私有设计文档、计划、评审记录 | 否 |
| `_local/scripts/` | 一次性脚本与排查工具 | 否 |
| `_local/legacy/` | 历史目录（仅本机保留） | 否 |
| `_local/artifacts/` | 过程产物（自检归档、`be2_work/`、`qwf_out/` 等） | 否 |
| `_local/startup/` | 本机启动/部署脚本（`start-qmclaw.*`、`deploy-local.*`、`启动命令.txt`） | 否 |

**留在仓库根、不许移动**的运行时目录：`state.db`（+ `-shm`/`-wal`）、
`sessions/`、`memories/`、`memory/`、`models/`、`logs/`、`images/`、`hooks/`、`cron/`、
`config/`、`pairing/`、`audio_cache/`、`image_cache/`、`cache/`、`.memsearch/`、`.dsh-orders/`、
`__pycache__/`、`.pytest_cache/`、`.tmp_state_pptx/`、`SOUL.md`、`auth.lock`
（Hermes 的 `HERMES_HOME` = 仓库根，移走会坏 agent）。

启动脚本按 `$PSScriptRoot` 反推仓库根（`<root>/_local/startup/` 上溯两级），不写死绝对路径。

## 约定

1. **开发任务用 sub-session 隔离**：不污染主 session 的日常对话
2. **模块化和结构化设计**：分层架构（前端/后端/测控），单一职责，API 优先
3. **前后端服务启停由人控制**：不自行启动或杀死前后端服务，这些操作要由人完成（Express :3002、Next.js :8081）
4. **复杂功能开发前先确认需求**：向用户提问明确需求、优先级、边界条件
5. **AGENTS.md 保持精简**：开发进展写入 `.Codex/progress.md`，AGENTS.md 只保留经用户确认的约定
   - **每次修改都要记录进 `.Codex/progress.md`**（改动内容、文件、验证结果、提交 hash）
6. 当一个同样的问题反复出现两次以上，就不要采用直接的思路去解决了，而是增加一些debug信息来辅助解决。
7. 当需求涉及较多修改时，先制定计划，由用户确认后才能执行。
8. 计划模式下，或者用户明确让制定计划的时候，需要多向用户提问，详细了解需求。
9. 当前系统有后端打印日志的过滤器，只有满足特定声明逻辑的日志才能被打印到后端的终端窗口里。所以当你要增加后端日志的时候，需要检查一下后端日志过滤器(server/src/index.ts)。
10. Python 解释器：用 `py -3.12`（Windows）或 `python3`（Linux/macOS）；**不要把个人机器的解释器绝对路径写进文档或代码**
11. **按节奏提交，不是每改一个文件就提交**：累计到一定量或碰上重要节点时 `git commit`。
    - **攒够 3~5 处改动**提交一次；一次任务 / 会话收尾前必须提交干净，不留半成品在树上
    - **重要操作前后必须各自提交一次**（提交作为回退点）：
      - 动约定、配置、依赖（`AGENTS.md`、`model_configs.json`、`.env`、`requirements.txt`）之后
      - 重构 / 批量替换之前先提交一版干净基线，之后提交一版结果
      - 删除文件、清理数据、改数据库或 memory 之前
      - 推送（`git push`）之前
    - 提交信息带 scope，例如 `fix(memory): 修正离线索引生成目录`、`feat(hermes): 模型接口与 agent 对齐`
    - **提交前必须确认没有把密钥加进去**：`.env` 已在 `.gitignore` 中，不要用 `git add -f` 强加；
      提交前和**推送前**各扫一遍（过滤掉 `.env.example.demo` 里的 `sk-xxxx...` 占位符）：
      `git grep --cached -E "sk-[a-zA-Z0-9]{20,}" | Select-String -NotMatch "sk-x{20,}"`
      一旦误提交并已推送，改历史无用，必须去平台吊销并轮换密钥
    - 只提交本次改动涉及的文件，不要 `git add -A` 把无关的构建产物（如 `tsconfig.tsbuildinfo`）一起带上
12. **git push 只能由人操作**：任何 `git push`（含 `--force`、子模块 push、tag push）一律不执行，只做本地 commit。
    - 需要推送时，把要推的分支与提交范围告诉用户，由用户自己执行。

# Compaction 压缩规则（自动压缩时生效）
## 摘要必须保留内容
1. 项目架构：QMC Pro / QMC Claw微服务分层定义、各个微服务调用关系（Agent、Workflow、Quantum、Analysis、Image、QubitClient）
2. 关键技术决策：自动驾驶类比的规则引擎+大模型混合方案
3. 未完成任务清单、当前开发阶段
4. 核心代码接口定义、重要实验测控逻辑


## 需要简化/丢弃
1. 反复调试的中间失败日志、重复报错
2. 临时探索性的非核心讨论
3. 无关闲聊内容
4. 确认已修复的bug

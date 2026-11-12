# 给项目来源方（上游仓库 / 实验室）的补足清单

> **生成时间**：2026-11-12
> **对应上游**：`QMC-AI/QMClaw` 分支 `sync/qmclaw-20261111`（`19faee0`）
> **我方状态**：该分支已整链合入本地并推送（合并 `6857ff3`，补丁 `cbf6aad`），离线自检 39 项全绿、qwf 体检 92 项全绿。
> **本文可直接转发**。每项都写清：现状（带证据）→ 影响 → 建议动作 → 怎么算补好了。

---

## 0. 一句话摘要（可直接粘进微信/邮件）

> 上游 `sync/qmclaw-20261111` 我方已全部合入并跑通（39 项离线自检 + 92 项 qwf 体检全绿）。
> 还差 **5 份只能你们给的东西**（LLM 配置文件、6 份设计文档里的 6 份、离线数据包、真机会话路径、上游 `.gitignore` 白名单），
> 另外**上游仓库自身有 9 处缺陷**我在本地已修并推了补丁，**需要回流到上游**，否则下次同步会带回来。
> 详细的证据、影响和验收方式见 `docs/UPSTREAM_GAP_LIST.md`。

---

## A. 只能由你们提供的东西（我方补不了）

### A1 🔴 `config/qubitclient.json` —— LLM/决策服务现在起不来

| | |
|---|---|
| **现状（实测）** | `qubitclient_service`（:3010）`/health` → **HTTP 503**，`status: degraded`，`error: "No LLM configuration found in qubitclient.json"`，`initialized: false` |
| **为什么不能自己造** | 该文件含**实验室内网地址与 API Key**，上游 `.gitignore` 已明确排除，**不得外传**（我们不会去索取密钥本身） |
| **影响** | ① `.qwf` 里的 **`decide` / `ask` 节点**（走 LLM 决策）在这台机器上**完全不可用**；② `_p2_llm_check.py` 的 6 条配置断言只能判 SKIP；③ `/api/services/health` 里 LLM 相关能力标 `unavailable` |

**建议动作（二选一）**：

1. **在实验室那台机器上放一份**（推荐）：把 `vendor/QubitClient/qubitclient.json.example` 拷到
   `qmclaw-server/config/qubitclient.json`，填 `url` / `api_key` / `llm.{base_url,api_key,model}`。
   *（本机模板已在 `vendor/QubitClient/qubitclient.json.example`；搜索顺序：`services/qubitclient_service/config/` → `qmclaw-server/config/` → `vendor/QubitClient/` → cwd。）*
2. **改成读环境变量**（更可持续，建议上游采纳）：像 `model_configs.json` 那样，
   把 `api_key` 换成 `${QMCLAW_QUBITCLIENT_API_KEY}` 这类写法，配置文件就可以入库了。

**验收**：`GET :3010/health` → `status: healthy`、`initialized: true`。

---

### A2 🟠 6 份设计文档 —— 上游 `.gitignore` 把它们排除了，但代码在引用

上游 `.gitignore` 的 `/docs/**/*.md` 规则把下列文档排除在版本控制外，**新 clone 的人拿不到**，
可代码注释和自检脚本都在引用它们：

| 文档 | 内容 | 被谁引用 |
|---|---|---|
| `docs/DSL_DESIGN.md` | **DSL 子系统总体设计**（本次同步的关键增量） | 代码注释 |
| `docs/DSL_EXPERIMENTS.md` | DSL 实验说明 | 代码注释 |
| `docs/BACKEND_ADAPTERS.md` | 后端适配器约定 | `dsl/backends/` |
| `docs/ARCHITECTURE_ROADMAP.md` | 阶段路线图 | `_phase13/14/17/18_check.py` |
| `docs/PLAN_PRODUCER_DEFAULT.md` | `/execute` producer 化方案 | `_phase16_check.py` |
| `docs/TUNEUP_FLOW.md` | 调优流程 | 脚本 |

**建议动作**：把这 6 份文档入库（**改 `.gitignore` 白名单，不要改文件名**）。
若属于未发表/内部材料不便公开，至少给**一份对接口径说明**（IR 字段表、budget 语义、后端调用约定），
否则第三方按 `.qwf` 写工作流只能靠猜。

**验收**：`git clone` 后这 6 个文件存在；`_phase13/14/16/17/18_check.py` 不再报"文档缺失"。

---

### A3 🟠 离线数据包 `qmclaw-server/data/offline_data/` —— 本机没有，离线分析用不了

| | |
|---|---|
| **现状（实测）** | 本机 `qmclaw-server/data/offline_data/` **目录不存在**；`analysis_service`(:3004) `/health` → **503 degraded**，`fallback_state: degraded_permanent`，**`offlineAvailable: false`**，`fallback_message: "LabRAD 不可用（已停止重连）"` |
| **参照** | `docs/RUNTIME_DEPS.md:23` 记的是上游机器：`D:\semester4\deploy\offline_data\` = **3 个日期目录 / 5,011 个 HDF5** |
| **影响** | 离线模式（LabRAD 不可用时用历史数据）、离线 DataVault 出图、本地无法复现历史曲线 |

**建议动作**：把 `offline_data` 打包给到本机（放 `qmclaw-server/data/offline_data/`），我方执行
`python -m services.common.offline_data_provider --generate-index` 生成索引
（目录里没有 `qubits.json`/`datasets.json`/`experiments.json`，不建索引每次启动都要扫 5,011 个文件）。

**验收**：`analysis_service /health` → `offlineAvailable: true`；`POST :3004/mode {"mode":"offline"}` 后可取到历史数据。

---

### A4 🟠 真机会话路径对不上 —— `analysis_service` 连不上 LabRAD，`quantum_service` 却正常

| | |
|---|---|
| **现状（实测）** | `quantum_service`(:3003)：`healthy`，**`labrad_connected: true`**，`session_path: ["","LQHL","test","20260822_device1"]`<br>`analysis_service`(:3004)：**degraded**，`session_path: []`，`datalab_connected: false`，`"LabRAD 不可用（已停止重连）。最后错误：未知错误"` |
| **配置现状** | `config/session.json` 写的是 `user=LQHL, path=["test","20260324"]`；`config/system.json` 的 `lqcs.session.default_path` 也是 `["test","20260324"]` |
| **矛盾点** | 设备上**实际存在**的会话是 `test/20260822_device1`（quantum_service 报的），而我们配置指向 `test/20260324`。分析服务按配置去连那个会话失败 → 退化成 `degraded_permanent` |

**建议动作**：**确认这台机器该用哪个 session 路径**（是 `test/20260324` 还是 `test/20260822_device1`？还是需要按实验切换？）。
确认后我方改 `config/session.json` + `config/system.json`（各 1 行），或由你们直接改。
若会话路径是**每次实验都变**的，建议上游把 session 做成**运行时参数**（`analysis_service` 已有
`POST /connect {"session_path": [...]}` 入口可用），别再依赖静态配置。

**验收**：`analysis_service /health` → `status: healthy`、`datalab_connected: true`、`session_path` 非空。

---

### A5 🟡 上游 `.gitignore` 白名单与自检清单互相矛盾（上游仓库自身问题）

上游 `.gitignore` 用 `/_*.py` 忽略根目录所有下划线脚本，只放行一份白名单，
**但仓库里实际跟踪着白名单之外的脚本**（当初用 `git add -f` 强加的）：

| 脚本 | 现状 | 被谁引用 |
|---|---|---|
| `/_qwf_gen_goal_check.py` | 被 `.gitignore` 排除，**但** `run_all_checks.py` 的 OFFLINE 清单与 `docs/OPERATIONS.md` §4 都列着 → 新 clone 后整项 SKIP | runner + 文档 |
| `/_p1_diff.py` | 白名单外，新 clone 不存在 | runner |
| `/_gen_spec_qwfs.py` | 白名单外，新 clone 不存在 | runner |
| `/_gen_device_profile.py` | 白名单外，新 clone 不存在 | `docs/OPERATIONS.md` §3.4 |
| `/_p3_e2e.py` | 白名单外，新 clone 不存在 | runner ONLINE + OPERATIONS §4 |

**建议动作**：把上面 5 个脚本**加进上游 `.gitignore` 的白名单并正常入库**（不要 `git add -f`），
或从 runner/文档引用里去掉。**二选一即可，但现状（新 clone 缺文件 vs 清单要求存在）必须收敛。**

> 我方本轮的处置：只把"无任何文档/清单引用"的 2 个出库（`_p1_diff.py`、`_gen_spec_qwfs.py`），
> 有引用的 3 个保留入库 —— 出库反而会造成新的文档漂移。

**验收**：`git clone` 上游后 `run_all_checks.py` 的 OFFLINE 39 项**没有一项**因为"文件不存在"而 SKIP。

---

## B. 上游仓库自身的缺陷（我方已修并推了补丁，**需要回流上游**）

> 我方补丁都在 `main`（`https://github.com/Secsix/QMClaw.git`），可直接 cherry-pick。
> **不回流的后果：下次同步会把这些 bug 原样带回来。**

| # | 缺陷 | 现象 | 影响 | 我方修法 |
|---|---|---|---|---|
| **B1** | printer 丢 `decide` 的 `from` | `dsl/textdsl/printer.py` 不打印 `from` | **决策节点看不到上游实验数据** —— 服务端 decision 分支只读 `node.depends`，`from` 一丢就退化成"纯选择"；画布存档/导出/任何"解析→打印→再解析"都会丢 | `printer.py` 的 decide 字段表加 `"from"`（8 行） |
| **B2** | `qwf_gen.py` 产物 lint 不过 | 普通模式只写 `max_wall`+`max_writes` | `qwf_gen.py` 是文档推荐的工作流入口，**它生成的每份 .qwf 都过不了 `qwf_lint.py`**（`E_NO_BUDGET`）。CI 没发现是因为只 lint `qwf_examples/` | budget 块始终写 `max_datasets` + 新增 `--max-datasets`（~8 行） |
| **B3** | 示例目录错位 | 服务端只读被 gitignore 的 `data/workflows/examples/`，产品示例在仓库根 `qwf_examples/` | **示例列表永远为空** | `qwf_store.py` 加 `example_dirs()/example_files()/find_example()` |
| **B4** | `goal`/`bounds` 画布往返丢失 | `ir_to_canvas` 不带这两个字段 | 打开→改→导出，**目标契约就没了** | `dsl/canvas_ir.py` 补带出/写回 + 前端 `QwfInfoPanel.tsx` |
| **B5** | 两个产品示例过不了自己的 `validate()` | `02_ramsey_t2`/`03_t1_fixed_zpa` 写 `max_writes 0` 却含 1 条 `measure` | 示例自相矛盾 | 示例改 `max_writes 1`（**没动引擎口径**） |
| **B6** | 回归段永远零命中 | `_p1_goal_dsl_check.py` 的 glob 指仓库根 `*.qwf`，示例在 `qwf_examples/` | **示例从来没被回归过**（B5 就是被它漏掉的） | 该脚本 glob 修正 + 新增第 7 段回归断言 |
| **B7** | 能力基线硬编码某台机器 | `services/common/capabilities.py` 的 `KNOWN_RUNTIME_EXPORTS`/`KNOWN_MISSING` 写死上游那台机器的 `.pyd` 导出情况 | 别的机器上能力探测结论不对 | **我方未改**（改成可配置/可探测需要动语义）→ **想请你们定：是给一份"能力基线配置文件"，还是让代码自己探测？** |

---

## C. 上游工程卫生问题（不阻塞，但每次同步都要手工处理）

| # | 问题 | 建议 |
|---|---|---|
| **C1** | **上游 CI 只跑 4 项**：`.github/workflows/checks.yml` 实际只跑 `_syntax_check.py` / `_docs_paths_check.py` / `_phase19_check.py` / `qwf_lint.py`；39 项 OFFLINE 全量自检**没进 CI** | 把 OFFLINE 清单接进 CI（能否满足依赖请确认 runner 环境）。B5 就是 CI 没覆盖才漏出来的 |
| **C2** | 上游跟踪了运行时产物：`services/task_queue/data/tasks/*.json` 有 10 个 `t-*.json` 被跟踪，而 `.gitignore:179` 已声明那是落盘目录 | 出库（`git rm --cached`），保留 `.gitignore` |
| **C3** | `docs/deploy_diff_tools/e3_tuneup18_edsl.py` 引用的产物路径在本机不存在 | 给正确路径，或把脚本出库 |
| **C4** | 上游 `.gitignore` 的"文档只留 3 篇 / `skills/` 整体忽略 / 忽略 `CLAUDE.md`·`AGENTS.md`"是**对方公开仓库的策略**，与本仓库的 17 篇 docs + 9 个 skills + 3 个约定文件冲突 | 本地已按"不照搬、放行本地路径"处理；若你们希望统一口径，请给一句话说明 |

---

## D. 引擎语义的两个待决问题（想请你们拍板）

| # | 问题 | 我方现状 | 建议 |
|---|---|---|---|
| **D1** | `canvas_to_qwf(doc, base_ir=...)` 用 `ir_diff` 判"用户改了什么"，但 `normalize()` 会补 `context:{}` 和 `effect:null`，`canvas_to_ir` 重建的 IR 不带 → **没改任何东西也会报"有改动"** | 生产路径不受影响（服务给的 `baseIr` 都是未 normalize 的原始 parse），已记录未改 | 建议在 `canvas_to_qwf` 里**两边都 normalize 后再比**（语义更一致） |
| **D2** | `normalize()` 对 **goal 工作流不幂等**（lowering 出来的 `repeat/if/assert` 没有 `effect` 键，`normalize` 两次结果不同） | `effect` 的消费点都带兜底，**执行语义与额度判定不受影响**，只是标记层面 | 建议 lowering 出来的语句也补 `effect`（或在 `normalize` 末尾统一补一遍） |

---

## E. 我方这边已完成 / 待你决定（不需要来源方动手）

**已完成并推送**

- 上游 `sync/qmclaw-20261111` 全量合入（合并 `6857ff3`），B1/B2 两个补丁（`cbf6aad`）。
- 离线自检 `run_all_checks.py` → **39 项 / 0 失败 / 1 跳过**（跳过的 1 项是上游 `.gitignore` 排除的 `_qwf_gen_goal_check.py`，见 A5）。
- qwf 功能体检 → **92 项断言全绿**（DSL 核心 / 画布往返 / 存档 CRUD / 服务 handler / 根目录工具 / 全仓 .qwf 解析）。
- 活接口体检（对着今天 20:25 启动的真实服务）→ **20/23 通过**，qwf 相关接口全通。

**需要你做 / 决定的两件事**

1. 🔁 **重启 `workflow_service`（:3008）**：它是 20:25 启动的，跑的是 B1 补丁**之前**的代码
   （补丁 20:33 才提交）。实测活服务导出的 `.qwf` 里 `decide.from` **仍然丢**；重启后再验一次就应该是"在"。
   （约定 3：服务启停由你操作。）
2. 📥 **要不要把体检脚本正式入库**：`_verify_qwf.py`（92 项，离线）与 `_verify_live.py`（活接口）
   目前是仓库根的 `_*.py`，被 `.gitignore` 排除，**只存在本地**。要入库的话我需要改 3 处：
   `.gitignore` 白名单、`run_all_checks.py` 的 OFFLINE 清单、`docs/OPERATIONS.md` §4 表格
   （`_docs_paths_check.py` 双向校验这三者必须一致）。

---

## F. 验收清单（补齐后逐条打勾）

| # | 项 | 期望 | 当前 |
|---|---|---|---|
| 1 | `GET :3010/health` | `healthy` / `initialized: true` | ❌ 503 degraded |
| 2 | `GET :3004/health` | `healthy` / `datalab_connected: true` / `session_path` 非空 | ❌ 503 degraded |
| 3 | `analysis_service /health` → `offlineAvailable` | `true` | ❌ false |
| 4 | 6 份设计文档 | 新 clone 后可见 | ❌ 缺 6 份 |
| 5 | `run_all_checks.py` OFFLINE 39 项 | 无"文件不存在"导致的 SKIP | ❌ 1 项 |
| 6 | 上游 CI | 覆盖 OFFLINE 全量 | ❌ 只 4 项 |
| 7 | `git clone` 上游后跑全量自检 | 0 FAIL | ⬜ 待修完再测 |
| 8 | `.qwf` 的 `decide` 节点 | 导出文本里 `from [$r1]` 还在 | ⬜ 需重启 workflow_service |
| 9 | `qwf_gen.py` 产物 | `qwf_lint.py` 0 error | ✅ 我方已修（待回流上游） |
| 10 | 写闸门 | `allow_execute=false` / `allow_set_params=false` | ✅ 实测保持关闭 |

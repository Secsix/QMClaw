# 给项目来源方（上游仓库 / 实验室）的补足清单

> **版本**：2026-11-12 22:30（第二轮更新）
> **对应上游**：`QMC-AI/QMClaw` 分支 `sync/qmclaw-20261111`
> **上游把分支重写了**：旧 tip `19faee0` → 新 tip `4fad224`（同一提交信息、同一父提交 `e3cb8fa`）。
> 我方已按新 tip 重新合并（合并提交 `af8795d`），本地保留上一轮的 K1–K8 修复。
> **本文可直接转发**。每项写清：现状（带证据）→ 影响 → 建议动作 → 怎么算补好了。

---

## 0. 一句话摘要（可直接粘进微信/邮件）

> 上游分支重写后我方已再次合并，**现在 39 项离线自检全部真跑通过（0 跳过）、qwf 体检 92 项全绿，
> qwf → 队列 → 真机服务整条链路实测打通（q21_7 的 iqraw 已跑到写闸门被拦下，红线按预期生效，未下发硬件指令）**。
> 剩下 **3 件只能你们给的东西**（LLM 配置文件、4 份设计文档、离线数据包），
> 外加 **上游仓库自身 8 处缺陷需要回流**（补丁我方已写好，可直接 cherry-pick），
> 以及 **CI 覆盖**这一项工程建议。

---

## A. 只能由你们提供的东西（我方补不了）

### A1 🔴 `config/qubitclient.json` —— LLM/决策服务起不来

| | |
|---|---|
| **现状（实测）** | `qubitclient_service`（:3010）`/health` → **HTTP 503**，`status: degraded`，`error: "No LLM configuration found in qubitclient.json"`，`initialized: false` |
| **为什么不能自己造** | 该文件含**实验室内网地址与 API Key**。上游不但排除了它，2026-11-12 还加了一条"兜底忽略**任意层级**的 `qubitclient.json`"（`c7ec2b9`）—— 即**明确不会入库** |
| **影响** | ① `.qwf` 里的 **`decide` / `ask` 节点**（走 LLM 决策）在这台机器上**完全不可用**；② `_p2_llm_check.py` 的 6 条配置断言只能判 SKIP；③ `/api/services/health` 里 LLM 相关能力标 `unavailable` |

**建议动作（二选一）**：

1. **在实验室那台机器上放一份**（推荐）：把 `vendor/QubitClient/qubitclient.json.example` 拷到
   `qmclaw-server/config/qubitclient.json`，填 `url` / `api_key` / `llm.{base_url,api_key,model}`。
   *（本机模板已在 `vendor/QubitClient/qubitclient.json.example`；搜索顺序：`services/qubitclient_service/config/` → `qmclaw-server/config/` → `vendor/QubitClient/` → cwd。）*
2. **改成读环境变量**（更可持续，建议上游采纳）：像 `model_configs.json` 那样，
   把 `api_key` 换成 `${QMCLAW_QUBITCLIENT_API_KEY}` 这类写法，配置文件就可以入库了。

**验收**：`GET :3010/health` → `status: healthy`、`initialized: true`。

---

### A2 🟠 还差 4 份设计文档（上游 11-12 补了 2 份）

上游 2026-11-12 在 `.gitignore` 里放行了 2 篇并入库：

| ✅ 已到位 | 内容 |
|---|---|
| `docs/ARCHITECTURE_ROADMAP.md`（602 行） | 阶段路线图 |
| `docs/PLAN_PRODUCER_DEFAULT.md`（66 行） | `/execute` producer 化方案 |

**仍然缺的 4 份**（上游 `.gitignore` 的 `/docs/**/*.md` 规则仍把它们排除在版本控制外，
但代码注释 / 自检脚本在引用）：

| ❌ 待补 | 内容 | 被谁引用 |
|---|---|---|
| `docs/DSL_DESIGN.md` | **DSL 子系统总体设计**（本次同步的关键增量） | 代码注释 |
| `docs/DSL_EXPERIMENTS.md` | DSL 实验说明 | 代码注释 |
| `docs/BACKEND_ADAPTERS.md` | 后端适配器约定 | `dsl/backends/` |
| `docs/TUNEUP_FLOW.md` | 调优流程 | 脚本 |

**建议动作**：把这 4 份入库（**改 `.gitignore` 白名单，不要改文件名**；照 11-12 那两篇的做法即可）。
若属于未发表/内部材料不便公开，至少给**一份对接口径说明**（IR 字段表、budget 语义、后端调用约定），
否则第三方按 `.qwf` 写工作流只能靠猜。

**验收**：`git clone` 后这 4 个文件存在；`_phase13/14/16/17/18_check.py` 不再报"文档缺失"。

---

### A3 🟠 离线数据包 `qmclaw-server/data/offline_data/` —— 本机没有，离线分析用不了

| | |
|---|---|
| **现状（实测）** | 本机 `qmclaw-server/data/offline_data/` **目录不存在**；analysis_service 连上真机后 `/health` 仍显示 **`offlineAvailable: false`** |
| **参照** | `docs/RUNTIME_DEPS.md:23` 记的是上游机器：`D:\semester4\deploy\offline_data\` = **3 个日期目录 / 5,011 个 HDF5** |
| **影响** | 离线模式（LabRAD 不可用时用历史数据）、离线 DataVault 出图、本地无法复现历史曲线 |

**建议动作**：把 `offline_data` 打包给到本机（放 `qmclaw-server/data/offline_data/`），我方执行
`python -m services.common.offline_data_provider --generate-index` 生成索引
（目录里没有 `qubits.json`/`datasets.json`/`experiments.json`，不建索引每次启动都要扫 5,011 个文件）。

**验收**：analysis_service `/health` → `offlineAvailable: true`；`POST :3004/mode {"mode":"offline"}` 后可取到历史数据。

---

### A4 ✅ 会话路径 —— 我方已实测解决（**请确认这个命名是否该改**）

**这一项我方已经自己测出来了**，但暴露了一个设计问题，想请你们确认：

| 候选路径 | 结果（实测 `POST :3004/connect`） |
|---|---|
| `["", "LQHL", "test", "20251103_LD_SQ"]`（**上游 11-12 版配置**） | ❌ `Registry path not found` |
| `["", "LQHL", "test", "20260324"]`（旧文档口径） | ❌ `Registry path not found` |
| `["", "LQHL", "test", "20260822_device1"]`（**quantum_service 报的设备当前会话**） | ✅ **Connected to DataLab** |

我方已把 `config/session.json` 与 `config/system.json` 改成本机实测可用的
`["test", "20260822_device1"]`，并验证 analysis_service 由 `degraded` 变为 **`healthy`**
（`datalab_connected: true`、`data_source: labrad`）。

**但想请你们回答**：

1. `20260822_device1` 是**这台机器当前**的会话（`20260822` 是日期，`device1` 是设备号）——
   换一台机器、换一个实验日期，这个名字就会变，对吗？
2. 如果是，**会话路径应该做成运行时参数**（`analysis_service` 已经有 `POST /connect {"session_path": [...]}`，
   我方就是用它连上的），而不是依赖静态配置。上游的 `services/common/session.py` 已经支持这个模式
   （`load_session_config` / `parse_session_path`），只是 `config/*.json` 里的默认值是写死的。

**建议动作**：确认"会话路径按机器/按实验可变"这一事实，并把默认配置里的写死路径当作**示例值**而不是事实来源；
更稳妥的是在 `analysis_service` / `quantum_service` 启动时**探测**当前可用会话并回填。

**验收**：`GET :3004/health` → `status: healthy`、`session_path` 非空、`data_source: labrad`。

---

### A5 🟡 上游 `.gitignore` 白名单与自检清单互相矛盾 —— **修好 1/5**

| 脚本 | 上游 11-12 状态 | 说明 |
|---|---|---|
| `/_qwf_gen_goal_check.py` | ✅ **已修**（白名单补 `!/_qwf_gen_goal_check.py` 并入库 141 行） | 我方本轮**首次真跑**它：**24 OK / 0 FAIL** —— 这是 goal lowering / manifest / plan 轮数的回归网 |
| `/_p1_diff.py` | ❌ 未修（仍在白名单外，靠 `git add -f` 强加） | 新 clone 上游仓库**不存在**，却被 `run_all_checks.py` / 文档引用 |
| `/_gen_spec_qwfs.py` | ❌ 未修 | 同上 |
| `/_gen_device_profile.py` | ❌ 未修 | `docs/OPERATIONS.md` §3.4 以路径引用它 → 文档漂移自检会红 |
| `/_p3_e2e.py` | ❌ 未修 | `run_all_checks.py` 的 ONLINE 清单 + `docs/OPERATIONS.md` §4 都引用 |

**建议动作**：把这 4 个也加进上游 `.gitignore` 白名单并正常入库（不要 `git add -f`）。

> 我方本轮的处置：**5 个全部纳入本仓库白名单**（`git check-ignore` 已验证 5 个都不再被忽略），
> 保证 clone 下来"清单引用的文件真实存在"。

**验收**：`git clone` 上游后 `run_all_checks.py` 的 OFFLINE 39 项**没有一项**因"文件不存在"而 SKIP。

---

## B. 上游仓库自身的缺陷（我方已修并推了补丁，**需要回流上游**）

> 我方补丁都在 `main`（`https://github.com/Secsix/QMClaw.git`），可直接 cherry-pick。
> **不回流的后果：下次同步会把这些 bug 原样带回来**（本次重写已经印证了这一点）。
> **截至上游新 tip `4fad224`，这 7 项一处都没修。**

| # | 缺陷 | 现象 | 影响 | 我方修法 | 状态 |
|---|---|---|---|---|---|
| **B1** | printer 丢 `decide` 的 `from` | `dsl/textdsl/printer.py` 不打印 `from` | **决策节点看不到上游实验数据** —— 服务端 decision 分支只读 `node.depends`，`from` 一丢就退化成"纯选择"；画布存档/导出/任何"解析→打印→再解析"都会丢 | `printer.py` 的 decide 字段表加 `"from"`（+8 行，含注释说明为什么必须印） | ❌ 上游未修 |
| **B2** | `qwf_gen.py` 产物 lint 不过 | 普通模式只写 `max_wall`+`max_writes` | `qwf_gen.py` 是文档推荐的工作流入口，**它生成的每份 .qwf 都过不了 `qwf_lint.py`**（`E_NO_BUDGET`） | budget 块始终写 `max_datasets` + 新增 `--max-datasets`（+8 行）<br>⚠️ **注意**：上游新脚本 `_qwf_gen_goal_check.py` 会自建 `Namespace` 调用 `qwf_gen.build()`，所以取值必须用 `getattr(args, "max_datasets", "")`，不能直接 `args.max_datasets`（否则该脚本 AttributeError —— 我方第一版就踩了，已修） | ❌ 上游未修 |
| **B3** | 示例目录错位 | 服务端只读被 gitignore 的 `data/workflows/examples/`，产品示例在仓库根 `qwf_examples/` | **示例列表永远为空** | `qwf_store.py` 加 `example_dirs()/example_files()/find_example()` | ❌ 上游未修 |
| **B4** | `goal`/`bounds` 画布往返丢失 | `ir_to_canvas` 不带这两个字段 | 打开→改→导出，**目标契约就没了** | `dsl/canvas_ir.py` 补带出/写回 + 前端 `QwfInfoPanel.tsx` | ❌ 上游未修 |
| **B5** | 两个产品示例过不了自己的 `validate()` | `02_ramsey_t2`/`03_t1_fixed_zpa` 写 `max_writes 0` 却含 1 条 `measure` | 示例自相矛盾 | 示例改 `max_writes 1`（**没动引擎口径**） | ❌ 上游未修 |
| **B6** | 回归段永远零命中 | `_p1_goal_dsl_check.py` 的 glob 指仓库根 `*.qwf`，示例在 `qwf_examples/` | **示例从来没被回归过**（B5 就是被它漏掉的） | 该脚本 glob 修正 + 新增第 7 段回归断言 | ❌ 上游未修 |
| **B7** | 能力基线硬编码某台机器 | `services/common/capabilities.py` 的 `KNOWN_RUNTIME_EXPORTS`/`KNOWN_MISSING` 写死上游那台机器的 `.pyd` 导出情况 | 别的机器上能力探测结论不对 | **我方未改**（改语义需你们拍板）→ **想请你们定：给一份"能力基线配置文件"，还是让代码自己探测？** | ⬜ 待拍板 |
| **B8** | **analysis_service 与 quantum_service 同时重启时，analysis 会永久 degraded** | 2026-11-12 实测：analysis 启动时 `labrad_connect()` 失败（LabRAD 此刻被 quantum_service 占用），其重连预算是**按次数**（5 次指数退避，约 1 分钟内耗尽）→ 5 次全落在占用窗口内 → 打印"LabRAD 不可用（**已停止重连**）"、状态永久 `degraded`、`session_path: []`、`data_source: offline`；**只能手动 `POST /3004/connect {"session_path": [...]}` 才恢复**（本轮实测该调用成功，health 立刻转 `healthy`） | ① 重启后 analysis 一直是 offline，出图/统计拿不到真机数据；② `offlineAvailable:false` 时**离线兜底也没有**（本机没装离线包）→ 实际是"两头都没有" | **我方未改**（属上游语义，改法要你们定）。代码里 2026-11-02 已修过同类问题（"从未成功连接过就跳过重连"），但**按次数的预算仍有这个洞** | ❌ 上游未修 |

---

## C. 上游工程卫生问题（不阻塞，但每次同步都要手工处理）

| # | 问题 | 状态 | 建议 |
|---|---|---|---|
| **C1** | **上游 CI 只跑 4 项**：`.github/workflows/checks.yml` 实际只跑 `_syntax_check.py` / `_docs_paths_check.py` / `_phase19_check.py` / `qwf_lint.py`；39 项 OFFLINE 全量自检**没进 CI** | ❌ 11-12 未变 | 把 OFFLINE 清单接进 CI（能否满足依赖请确认 runner 环境）。B5 就是 CI 没覆盖才漏出来的 |
| **C2** | 上游跟踪了运行时产物：`services/task_queue/data/tasks/*.json` 有 10 个 `t-*.json` 被跟踪 | ✅ **11-12 已修**（全部出库） | 无需再动（我方上一轮也已出库，两边一致） |
| **C3** | `docs/deploy_diff_tools/e3_tuneup18_edsl.py` 引用的产物路径在本机不存在 | ❌ 未变 | 给正确路径，或把脚本出库 |
| **C4** | 上游 `.gitignore` 的"只公开 README/install + 运维/适配文档"是**对方公开仓库的策略**，与本仓库的 17+ 篇 docs + 9 个 skills + 3 个约定文件冲突 | ❌ 未变（11-12 只是多放行了 2 篇） | 本地已按"不照搬、放行本地路径"处理；若要统一口径请给一句话说明 |

---

## D. 引擎语义的两个待决问题（想请你们拍板）

| # | 问题 | 我方现状 | 建议 |
|---|---|---|---|
| **D1** | `canvas_to_qwf(doc, base_ir=...)` 用 `ir_diff` 判"用户改了什么"，但 `normalize()` 会补 `context:{}` 和 `effect:null`，`canvas_to_ir` 重建的 IR 不带 → **没改任何东西也会报"有改动"** | 生产路径不受影响（服务给的 `baseIr` 都是未 normalize 的原始 parse），已记录未改 | 建议在 `canvas_to_qwf` 里**两边都 normalize 后再比**（语义更一致） |
| **D2** | `normalize()` 对 **goal 工作流不幂等**（lowering 出来的 `repeat/if/assert` 没有 `effect` 键，`normalize` 两次结果不同） | `effect` 的消费点都带兜底，**执行语义与额度判定不受影响**，只是标记层面 | 建议 lowering 出来的语句也补 `effect`（或在 `normalize` 末尾统一补一遍） |

---

## E. 我方这边已完成 / 待你决定

**已完成并推送**

- 上游新 tip `4fad224` 已再合（合并提交 `af8795d`），上一轮的 K1–K8 修复全部保留。
- 离线自检 `run_all_checks.py` → **39 项 / 0 失败 / 0 跳过**（`_qwf_gen_goal_check.py` **首次真跑**通过：24 OK / 0 FAIL）。
- qwf 功能体检 → **92 项断言全绿**。
- 活接口体检 → qwf 全链路通（`qwf_examples` / `parse_qwf` / `validate` / `qwf_to_canvas` / `canvas_to_qwf` / `run_ir` dryRun / `workflows` / `tasks/lane` / `stmt_to_qwf`）。
- **analysis_service 已从 degraded 变为 healthy**（实测会话路径 `["","LQHL","test","20260822_device1"]`）。

**需要你做 / 决定**

1. 🔁 **重启 `workflow_service`（:3008）**：它 20:25 启动，跑的是 printer 补丁（B1）**之前**的代码。
   实测活服务导出的 `.qwf` 里 `decide.from` **仍然丢**；重启后应变成"在"（约定 3：服务启停由你操作）。
2. 🔁 **重启 `analysis_service`（:3004）**（可选）：我方已用 `POST /connect` 让它连上真机了，
   但**配置里写的是本机路径**，重启后它能自动连上（配置已改）。
3. 📥 **要不要把体检脚本正式入库**：`_verify_qwf.py`（92 项离线）与 `_verify_live.py`（活接口）
   目前是仓库根的 `_*.py`，被 `.gitignore` 排除，**只存在本地**。要入库需改 3 处并保持一致：
   `.gitignore` 白名单、`run_all_checks.py` 的 OFFLINE 清单、`docs/OPERATIONS.md` §4 表格。

---

## F. 验收清单（补齐后逐条打勾）

| # | 项 | 期望 | 当前 |
|---|---|---|---|
| 1 | `GET :3010/health` | `healthy` / `initialized: true` | ❌ 503 degraded（缺 `qubitclient.json`） |
| 2 | `GET :3004/health` | `healthy` / `datalab_connected: true` | ✅ **已达成** |
| 3 | analysis_service `offlineAvailable` | `true` | ❌ false（缺离线数据包） |
| 4 | 6 份设计文档 | 新 clone 后可见 | ⚠️ 2/6（缺 `DSL_DESIGN` / `DSL_EXPERIMENTS` / `BACKEND_ADAPTERS` / `TUNEUP_FLOW`） |
| 5 | `run_all_checks.py` OFFLINE 39 项 | 无"文件不存在"导致的 SKIP | ✅ **已达成**（0 跳过） |
| 6 | 上游 CI | 覆盖 OFFLINE 全量 | ❌ 只 4 项 |
| 7 | `git clone` 上游后跑全量自检 | 0 FAIL | ⚠️ 4 个脚本仍缺（白名单漏放行） |
| 8 | `.qwf` 的 `decide` 节点 | 导出文本里 `from [$r1]` 还在 | ⬜ 需重启 workflow_service |
| 9 | `qwf_gen.py` 产物 | `qwf_lint.py` 0 error | ✅ 我方已修（**待回流上游**） |
| 10 | 写闸门 | `allow_execute=false` / `allow_set_params=false` | ✅ 实测保持关闭（q21_7 实跑在闸门处被拦下） |
| 11 | qwf → 队列 → 真机服务链路 | `run_ir` 真跑到 quantum_service 才被闸门拦下（而不是中途断链） | ✅ **已达成**（task_id `task_958809c9fa85`，`read_only` 拦截，无硬件指令下发） |
| 12 | analysis_service 启动自愈 | 重启后自动连上 LabRAD，不需手动 `/connect` | ❌ 永久 degraded（见 B8） |

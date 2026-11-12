# 同步分支 `sync/qmclaw-20261111` 合并计划（DSL 全链合入版）

> 对比对象：[QMC-AI/QMClaw](https://github.com/QMC-AI/QMClaw)
> - `main` = `e3cb8fa`（= 本地 `origin/main`，**零增量**）
> - `master` = `7ba10ea`（`main` 的祖先，更旧，无独有内容）
> - `sync/qmclaw-20261111` = `19faee0`（2026-11-11，**唯一增量**）
>
> 本地基线：`main` = `b76d407`（领先 `origin/main` 7 个提交）
> 已 fetch 为本地只读引用 `qmclaw-ai/sync-qmclaw-20261111`
>
> 本文件是**计划**，未执行任何合并。

---

## 0. 目标与成功标准

**目标**：把 `sync/qmclaw-20261111` 的全部特性合入本地 `main`，**DSL 子系统必合**（含其整条依赖链），同时守住本地三条硬约束：真机写闸门默认关闭、实验默认命令按用户决定照合、本地 7 个提交（S21 读数工具 / 单比特体检 / 离线出图 skill）不受损。

**成功标准**
1. `git log` 中本地 7 个提交仍在，且 `git diff` 显示其 39 个文件内容未被远端覆盖。
2. DSL 四个入口可用且自洽：文本 `.qwf` → IR → validate → run_ir；前端画布可导入/导出 `.qwf`；`qwf_lint.py` 对 5 个产品范例 0 error。
3. `fallback_config.json` 的 `quantum_service.actuation.allow_execute` 与 `allow_set_params` 均为 `false`；`POST :3003/execute` 仍返回 `read_only`。
4. `config/session.json` / `config/system.json` 的 `default_path` 保持本地 `["test","20260324"]`。
5. `experiment_configs.json` 采用远端版本（用户已确认照合）。
6. 离线自检全绿：`_syntax_check.py`、`_docs_paths_check.py`、`qwf_lint.py`、`run_all_checks.py` 的 OFFLINE 套件。
7. 工作区干净、无密钥入库、`.claude/progress.md` 记录完整。

---

## 1. 事实基线（全部经命令核实）

| 项 | 值 |
|---|---|
| 共同祖先 | `e3cb8fa` |
| 远端增量 | 1 个提交 `19faee0`，224 文件（新增 183 / 修改 41 / **删除 0**），+43922 / −846 |
| 其中服务端 Python 新增行 | 21966 |
| 本地领先 | 7 个提交，39 文件，+4299 |
| **双方都改的文件** | **仅 `.gitignore` 1 个** |
| 合并冲突 | `git merge-tree --write-tree` 实测：`.gitignore` 内容冲突 1 处，其余自动合并 |

### 1.1 DSL 子系统全貌（必合主体）

`Agentic Workflow/qmclaw-server/services/workflow_service/dsl/`，**31 文件 / 7975 行**：

| 组 | 文件 | 行数 |
|---|---|---|
| IR 核心 | `ir.py` 269 / `const.py` 202 / `helpers.py` 78 / `registry.py` 50 / `pyliteral.py` 21 | 620 |
| 文本 DSL | `textdsl/lexer.py` 58 / `parser.py` 445 / `printer.py` 348 / `__init__.py` 4 | 855 |
| 校验与 lint | `validate.py` 477 / `lint.py` 647 | 1124 |
| 编译与执行 | `compile.py` 436 / `executor.py` 1516 / `plan.py` 180 / `dryrun.py` 195 / `budget.py` 116 | 2443 |
| 画布桥接 | `canvas_ir.py` 497 | 497 |
| 后端适配 | `backends/base.py` 124 / `backends/lqcs.py` 799 / `backends/__init__.py` 46 | 969 |
| 策略与元 DSL | `goal.py` 347 / `plan.py` / `llm.py` 142 / `autorevert.py` 258 / `manifest.py` 237 / `edsl.py` 190 / `capabilities.py` 133 / `quantities.py` 103 | 1410 |

对外 API（`dsl/__init__.py` 的 `__all__`）：`parse_qwf` / `normalize` / `validate` / `run_ir` / `ir_from_canvas` / `ir_to_canvas` / `canvas_to_qwf` / `ir_equivalent` / `lint_ir` / `lint_text` / `plan_ir` / `Budget` / `DryRunProvider` / `LqcsAdapter` / `WorkflowBuilder` 等。

### 1.2 DSL 的依赖边界（实测导入关系）

- DSL → `services.common.profile`（2 处）、`services.common.metrics`（1）、`services.common.replay`（1）、`services.common.qubit_state`（1）
- DSL → `dsl.backends.lqcs` **直连 `http://127.0.0.1:3003`**（`/health`、`/execute`、提交后轮询 `/jobs/{handle}`）→ **必须同批合入 `quantum_service/server.py` 的 `/jobs` 端点**
- `workflow_service/server.py` → `task_queue.ledger.manifest_hash` → **必须同批合入 `task_queue/ledger.py` 与 `lane.py`**
- `common/*` **不反向依赖 DSL**（`runrecord.py`/`service_groups.py` 只提到 `dsl` 字样，非 import）→ 无循环依赖，可整链一起合
- 网关 `src/services/serviceProxy.ts` 新增 **15 条代理路由**（`/workflow/validate|parse_qwf|ir_from_canvas|qwf_*`、`/workflows` CRUD、`/workflow/run_ir`、`/tasks/lane`、`/tasks/lease/release`、`/quantum/jobs/:id(+abandon)`、`/quantum/qubit/apply_dataset`）→ 前端依赖它们，必须同批
- 前端 `qwfBridge.ts`（218 行）→ 依赖后端 `/workflow/qwf_to_canvas` 等 → 同批

**结论：DSL 与其 6 条依赖链无法拆开，必须整链合并**（与用户决策一致）。

### 1.3 `/execute` 语义变更（producer 化）的调用方适配

远端把 `/execute` 默认改为**异步**（立即返回 `job_id`）。已实测远端同步改造了**全部**调用方，本地不需要额外适配：

| 调用方 | 远端适配方式 |
|---|---|
| `services/client_proxy.py` | 加 `"wait": True` |
| `hermes_service/quantum_toolset.py` | 加 `"wait": True`（2 处） |
| `qca_service/adapter.py` | 加 `"wait": True`（2 处） |
| `hermes_service/cron.py` | payload 加 `"wait": True` |
| `agent_service/adapter.py` | 新增 `_poll_job()` 兜底轮询 |
| 前端 `lib/api.ts` | 新增 `isQueuedJob` / `waitForQuantumJob` / `flattenQuantumJob`，`executeExperiment` 自动轮询 |

### 1.4 缺失设计文档（用户已确认：接受缺失，先合代码）

远端 `.gitignore` 隐藏了 5 个被代码反复引用的文档，仓库里没有：

`docs/DSL_DESIGN.md`（被 `dsl/__init__.py`、`task_queue/ledger.py`、`qwf_run.py` 引用）、`docs/ARCHITECTURE_ROADMAP.md`（被 10+ 个 `common/*` 引用）、`docs/BACKEND_ADAPTERS.md`、`docs/PLAN_PRODUCER_DEFAULT.md`（被 `common/exec_policy.py` 引用）、`docs/TUNEUP_FLOW.md`。

处理：合入代码，在 `docs/BACKLOG.md` 新增一条 G 组待办记录这 5 个缺口，在 `.claude/progress.md` 登记。**不**自行编写替代文档冒充原文。

---

## 2. 执行步骤（每步 = 一个提交 = 一个回退点）

### S0　干净基线与备份

1. 提交当前工作区未提交改动（`workflowStore.ts` +9 行、`next.config.js`、`docs/QUBIT_HEALTH_REPORT_REQ.md` 已暂存、`.claude/progress.md`）。
   - ⚠️ `workflowStore.ts` 远端也改了（+106 行）→ **先提交再合并**，否则工作区与合并双线冲突。
   - `tools/single_qubit_auto_calibration/`（17 文件，未跟踪）与 `docs/SINGLE_QUBIT_AUTO_CALIBRATION_DESIGN.md`（未跟踪）**一并提交**，作为本地标定实现与实验室判据规范入库（见 §3 D1）。
2. `git tag backup/pre-sync-20261111-merge`（指向 `b76d407`）。
3. 提交信息：`chore(sync): 提交合并前基线（本地标定工具与体检文档）`

**验收**：`git status` 干净；`git tag -l backup/pre-sync-20261111-merge` 存在。

### S1　合并远端分支（整链，一次到位）

```powershell
git merge --no-ff qmclaw-ai/sync-qmclaw-20261111 -m "merge(sync): 同步 2026-11-11 分支（DSL 子系统 + 治理/回放/台账 + 标定自动化）"
```

**预期唯一冲突**：`.gitignore`（双方各追加）。解法见 §3。

**提交信息**：`merge(sync): 同步 2026-11-11 分支（DSL 子系统 + 治理/回放/台账 + 标定自动化）`

**验收**：
```powershell
git diff --stat backup/pre-sync-20261111-merge HEAD   # 应看到 224 文件级增量
git log --oneline -1                                # merge commit
# 本地 7 提交仍在
git log --oneline backup/pre-sync-20261111-merge~7..backup/pre-sync-20261111-merge | Measure-Object
# 远端未删除任何文件（实测 diff-filter=D 为空）
git diff --name-status --diff-filter=D backup/pre-sync-20261111-merge HEAD
```

### S2　`.gitignore` 冲突解法与本地路径豁免

远端 `.gitignore` 新增约 175 行，其中包含**会屏蔽本地工作流**的规则，必须处理：

| 远端规则 | 对本地的影响 | 处理 |
|---|---|---|
| `/docs/**/*.md`（仅放行 OPERATIONS / ADAPTER_GUIDE / DEPLOY_LOCAL 三篇） | 本地新增 `docs/*.md` 会被静默忽略 —— **包括本计划文件** | 改为只忽略远端新增的论文材料目录，**放行本地 `docs/` 顶层 md** |
| `/skills/` | 本地 `skills/s21-photo-reader`、`skills/lqms-offline-datavault-plotting` 已跟踪不受影响，但**新增 skill 文件会被忽略** | 改为 `/skills/*/out/` 之类的窄规则，保留 `skills/` 跟踪 |
| `/CLAUDE.md` `/AGENTS.md` `/SOUL.md` `/Agentic Workflow/qmclaw-server/README.md` | 已跟踪文件不受影响，但新增/修改说明文件受限 | 放行本地这三个约定文件 |
| `/*.qwf`（根目录） | `qwf_examples/*.qwf` 不受影响（已实测 `check-ignore` 判定 ok） | 保留 |
| `/_*.py` + 18 条 `!` 例外 | 48 个 `_*.py` 中已跟踪的不受影响；`_phase2b_check.py` 等不匹配例外规则但已跟踪 | 保留规则，另按 §3 D7 清理真正的一次性脚本 |
| `/Agentic Workflow/qmclaw-server/services/task_queue/data/tasks/` | 远端提交了 10 个运行时任务 json（与自己的 ignore 规则矛盾） | 按 §3 D7 从跟踪中移除这 10 个文件 |

**合并解法**：以远端 `.gitignore` 为底，**追加**本地原有两条（`/.pylibs/`、`/tools/s21_photo_reader/_tmp_test/`），并把上表中「会屏蔽本地工作流」的 3 条规则替换为放行本地路径的窄规则。**不**用 `git checkout --theirs` 一把梭。

**验收**（临时 git 仓库实测法，合并后在仓库根跑）：
```powershell
git check-ignore -v -- docs/SYNC_20261111_MERGE_PLAN.md      # 期望：无输出（未被忽略）
git check-ignore -v -- skills/s21-photo-reader/SKILL.md      # 期望：无输出
git check-ignore -v -- CLAUDE.md                            # 期望：无输出
git check-ignore -v -- qwf_examples/01_iqraw_contrast.qwf   # 期望：无输出
git check-ignore -v -- tools/s21_photo_reader/_tmp_test/x    # 期望：有输出（应被忽略）
```

### S3　配置红线修正（合并后立即改）

| 文件 | 合并后状态 | 动作 |
|---|---|---|
| `config/fallback_config.json` → `quantum_service.actuation.allow_execute` | 远端 = `true` | **改回 `false`** |
| 同上 → `allow_set_params` | 远端 = `true` | **改回 `false`** |
| 同上 → `require_task` / `writer_mode` / `replay_auto_record` / `execute_default` | 远端新增，默认 `false`/`shadow`/`off`/`async` | 保留（默认值安全） |
| 同上 → `analysis_service.retry` | 远端 `max_attempts 5→30`、`base_delay 2→3`、`max_delay 30→15` | **恢复 `max_attempts: 5`**（见 §5 假设 A1） |
| `config/session.json` → `data.session.path` | 远端 `["test","20251103_LD_SQ"]` | **恢复 `["test","20260324"]`** |
| `config/system.json` → `data.session.default_path` | 远端同上 | **恢复 `["test","20260324"]`** |
| `config/model_configs.json` | 远端：`baseUrl: "${QMCLAW_DEEPSEEK_BASE_URL}"` + vision/function_calling 能力 | 保留（配套 `env_expand.py` / `envExpand.ts` 已合入；变量未设置时回落官方地址，行为安全） |
| `config/experiment_configs.json` | 远端版 | **照合**（用户已确认） |
| `config/device_profile.json`、`lab_criteria.json`、`registry_bounds.json`、`registry_rollback_policy.json` | 远端新增 | 保留 |

**提交信息**：`fix(config): 恢复真机写闸门默认关闭与本地会话路径（合并回归）`

**验收**：
```powershell
# 写闸门
Select-String -Path 'Agentic Workflow\qmclaw-server\config\fallback_config.json' -Pattern 'allow_execute|allow_set_params'
# 期望：quantum_service 段两处均为 false；analysis_service 段保持 false
# 会话路径
Select-String -Path 'Agentic Workflow\qmclaw-server\config\session.json' -Pattern '20260324'
```

### S4　运行时产物出库（一次性脚本与任务数据）

1. `git rm --cached` 远端提交的 10 个 `services/task_queue/data/tasks/t-*.json`（远端自己的 `.gitignore` 已声明该目录是运行时产物）。
2. 迁移/出库明显一次性的根目录脚本（保留 CI 与 runner 依赖的）：
   - **保留**：`_syntax_check.py`、`_docs_paths_check.py`、`_phase19_check.py`、`_phase2*.py`…（CI 的 `checks.yml` 与 `run_all_checks.py` 的 `OFFLINE` 清单引用）、`qwf_lint.py`、`qwf_gen.py`、`qwf_run.py`、`run_all_checks.py`、`reg_snapshot.py`
   - **出库**（`git rm --cached`，文件留本地）：`_p1_diff.py`、`_gen_spec_qwfs.py` 等一次性产物脚本
   - **实际执行结果与原计划有出入，以证据为准**（2026-11-12 复核）：
     原计划列了 4 个出库对象，逐一核查后**只出库 2 个**，另外 2 个**保留入库**——
     出库的前提是「无引用」，而这两个有引用：
     - `_gen_device_profile.py` —— `docs/OPERATIONS.md` §3.4 以路径 `D:\qmclaw\_gen_device_profile.py` 引用它；
       `_docs_paths_check.py` 要求新克隆后该路径真实存在 → 出库会让 CI 的文档漂移自检失败
     - `_p3_e2e.py` —— 同时列在 `docs/OPERATIONS.md` §4 表格与 `run_all_checks.py` 的 `ONLINE` 清单；
       `_docs_paths_check.py` 双向校验二者一致 → 出库会造成清单漂移
     > 背景：上游 `.gitignore` 用 `/_*.py` 只放行一份白名单，这 4 个脚本**都不在白名单里**
     > （即上游本意是不入库，却用 `git add -f` 强加进了仓库）——详见 `docs/BACKLOG.md` J7。
3. 保留 `自动化单比特标定/`（17 文件，可移植真机标定包，含 `qwf_auto_calib.py` 11 节点链）**全部入库**。

**提交信息**：`chore(sync): 运行时产物出库，保留 CI 与 DSL 自检依赖的检查脚本`

### S5　文档补录

1. `docs/BACKLOG.md` 新增条目：5 个被 gitignore 隐藏的设计文档缺口（`DSL_DESIGN.md` / `ARCHITECTURE_ROADMAP.md` / `BACKEND_ADAPTERS.md` / `PLAN_PRODUCER_DEFAULT.md` / `TUNEUP_FLOW.md`），注明「代码已引用但文档未入库」。
2. `docs/OPERATIONS.md` 已由远端提供（997 行）——保留；若 §4 删改检查脚本清单，**必须同步改 `run_all_checks.py` 的 `OFFLINE` 列表**，否则 `_docs_paths_check.py` 会 FAIL（它双向校验二者一致）。
3. `docs/merge_verify_tools/`（13 个历史合并验证脚本）——**保留不删**，那是本地回归资产（远端也未删，实测 `diff-filter=D` 为空）。
4. `docs/DEPLOY_LOCAL.md`、`docs/deploy_diff_tools/` 新增探针脚本 —— 保留入库。
5. `docs/SYNC_20261111_MERGE_PLAN.md`（本文件）——入库。

**提交信息**：`docs(sync): 记录合并计划与缺失设计文档待办`

### S6　离线验收（全程不起服务）

**解释器**：本机 `D:\semester4\deploy\qmclaw\qmclaw\python.exe`（CLAUDE.md 约定 10）与 `C:\Users\HUAWEI\.conda\envs\qmclaw\python.exe`（AGENTS.md）**均不存在**；实际可用为
`C:\Users\lqcs\Programs\Python\Python311\python.exe`（3.11.9，已装 numpy 1.26.4 / requests / yaml）。
→ 执行时用该解释器；并按 AGENTS.md 约定「由用户确认的约定才写进 AGENTS.md」，**在 S6 末尾向用户确认后**再更新 `CLAUDE.md` 约定 10 的解释器路径（不擅自改约定文件）。

```powershell
$py = 'C:\Users\lqcs\Programs\Python\Python311\python.exe'

# 1) 全仓 Python 语法（CI 同款）
& $py _syntax_check.py

# 2) 文档漂移（路径存在性 + 自检清单与 runner 一致）
& $py _docs_paths_check.py

# 3) qwf 产品范例 lint 必须 0 error
& $py qwf_lint.py (Get-ChildItem qwf_examples\*.qwf | ForEach-Object { $_.FullName })

# 4) DSL 离线自检全套（run_all_checks 的 OFFLINE 清单）
& $py run_all_checks.py

# 5) 环境变量展开单测
& $py 'Agentic Workflow\qmclaw-server\tests\test_model_config_env.py'

# 6) 密钥扫描（提交前 + 推送前各一次）
git grep --cached -E "sk-[a-zA-Z0-9]{20,}" | Select-String -NotMatch "sk-x{20,}"
```

**已知会 WARN 不 FAIL 的项**：`_docs_paths_check.py` 对文档里他人机器的解释器路径只记 WARN（脚本自带该逻辑）。

### S7　前端构建（离线，不起服务）

```powershell
cd 'Agentic Workflow\qmclaw-web'
npm run build
```
`node_modules` 已存在；远端**未改动** `package.json`（无新依赖），仅新增 18 个前端文件。构建失败则先看是否 `next.config.js` 的 `serverComponentsExternalPackages` 与 Next 14 的 `experimental` 键冲突（本地未提交改动已把它移进 `experimental`，合入后需复核）。

**实际执行结果（S7 已完成）**：构建确实失败，暴露了计划里预判的那个键名冲突，但真实情况比预判的更棘手 —— **三种写法里只有一种能构建成功**：

| 写法 | Next 14.2.35 上的结果 |
|---|---|
| 顶层 `serverExternalPackages`（**上游 `19faee0` / `e3cb8fa` 的写法**） | 构建能过，但报 `Unrecognized key(s) in object: 'serverExternalPackages'`，配置被整体丢弃（这是 Next 15 才有的键名） |
| `experimental.serverComponentsExternalPackages`（**合并前基线 `08dce50` 的写法**） | **构建直接失败** —— Next 14.2 的 `optimizePackageImports` 默认列表已含 `'recharts'`，其条目会被并入 `transpilePackages`，两者撞车后报 `The packages specified in the 'transpilePackages' conflict with the 'serverComponentsExternalPackages': recharts` |
| `experimental.optimizePackageImports: ['recharts']` | **构建成功且无任何告警** ✅ |

**处置**：采用第三种写法，并在配置里写明另外两种各自为什么不行（防止下次同步被改回去）。
同步记入 `docs/BACKLOG.md` J10。

> 注：该问题**不是本次同步引入的** —— 合并前的基线 `08dce50` 已改成第二种写法，同样构建失败。
> 本轮是首次真正执行 `next build` 才暴露出来。

**验证**：`next build` → exit 0、stderr 空（无告警）；产物 `/` 252 kB / First Load JS 339 kB。
构建产物 `.next/` 与 `tsconfig.tsbuildinfo` 已被 `.gitignore` 覆盖，未污染工作树。

### S8　人工验收（由人起服务，我只提供清单）

见 §6 清单。

**本轮额外做掉的 4 个上游缺陷**（详细记录见 `docs/BACKLOG.md` K 组）：

| # | 上游缺陷 | 修复落点 |
|---|---|---|
| **K1** | 服务端只读被 `.gitignore` 排除的 `data/workflows/examples/`，而产品示例在仓库根 `qwf_examples/` → 示例列表永远为空 | `qwf_store.py`（新增 `example_dirs()` / `example_files()` / `find_example()`，运行期目录优先、仓库根回落）+ `server.py` + `tools/check_examples_canvas.py` |
| **K2** | `goal` / `bounds` 在画布往返里被静默丢弃（打开→改→导出，目标契约就没了） | `dsl/canvas_ir.py`（`ir_to_canvas` 带出、`canvas_to_ir` 写回）+ 前端 `QwfInfoPanel.tsx` |
| **K3** | `02_ramsey_t2` / `03_t1_fixed_zpa` 声明 `max_writes 0` 却含 1 条 `measure`，过不了自己的 `validate()`（引擎额度口径 = 实验语句条数） | 两个示例的 `max_writes` 改 1 + 注释说明口径；**未改引擎语义** |
| **K4** | `_p1_goal_dsl_check.py` 的回归 glob 是仓库根 `*.qwf`，示例实际在 `qwf_examples/` → 回归段永远零命中（K3 就是被它漏掉的） | 该脚本：glob 修正 + 仓库根兜底 + 新增第 7 段（K1/K2 回归断言）。**未新增检查项**，OFFLINE 仍 39 项 |

### S9　收尾

1. `.claude/progress.md` 按 CLAUDE.md 约定 5 记录：改动内容、文件、验证结果、提交 hash。
2. `git status` 确认干净；`git tag backup/post-sync-20261111-merge`。
3. 推送前再扫一次密钥，然后 `git push`（是否推送由人决定）。

---

## 3. 决策点与取舍（全部已定，按此执行）

| 编号 | 决策 | 结论 | 理由 |
|---|---|---|---|
| **D1** | 单比特标定实现 | **远端 `自动化单比特标定/` 全量入库 + 本地 `tools/single_qubit_auto_calibration/` 与 `docs/SINGLE_QUBIT_AUTO_CALIBRATION_DESIGN.md` 一并提交入库**，两者并存 | 远端代码可跑、可移植（17 文件）；本地设计文档含实验室原始判据（visibility>0.05、swing≥0.15 且 ≥4σ）与 11 节点链路规范，是远端缺的「实验室口径」。**不是重复实现**：一个是对外出图/链路的工程实现，一个是判据与流程规范 |
| **D2** | `quantum_service/server.py` 大改（+1927） | **合** | DSL 的 `backends/lqcs.py` 直连 `:3003/jobs/{id}`，不同批则 DSL 不可用 |
| **D3** | DSL 子系统 | **合**（用户明确要求） | 唯一重大增量 |
| **D4** | 前端画布新节点（18 文件 +2800） | **合** | `DslUnknownNode` / `qwfBridge` 依赖 DSL 节点类型；不配则画布出悬空节点 |
| **D5** | `experiment_configs.json` 默认命令 | **照合**（用户已确认） | 防御厂商函数静默回写寄存器；真机回归由用户在 S8 执行 |
| **D6** | `analysis_service.retry.max_attempts` 5→30 | **恢复 5** | 真机失败重试行为改变，30 次 × 3s 会长时间占用设备。**列为假设 A1，若你要照合请说一声** |
| **D7** | 一次性脚本与任务数据 | **出库**（`git rm --cached`） | 远端 `.gitignore` 已声明它们是运行时产物 |
| **D8** | 5 个缺失设计文档 | **接受缺失**，记入 BACKLOG | 用户已确认；不自行编写冒充原文 |
| **D9** | 缺失的 `tools/single_qubit_auto_calibration` 与远端标定目录的目录名差异 | **不统一** | 各自有独立语义，改名收益低于成本 |

---

## 4. 冲突预判与解法

| 冲突点 | 概率 | 解法 |
|---|---|---|
| `.gitignore` | **确定**（`merge-tree` 实测） | 手工并集，见 §2 S2；同时按表放行本地路径 |
| `config/fallback_config.json` | 语义冲突（远端改 true vs 本地约定 false） | 合并后立即改回 false（S3） |
| `services/common/config.py` | 可能（双方都改过） | 取远端版本后核对本地 `get_actuation_flags` 行为未退化；本地无其他改动，实际不会冲突 |
| `services/workflow_service/server.py` | 可能（本地 7 提交未触碰 → **实际不会冲突**） | 直接取远端版 |
| `Agentic Workflow/qmclaw-web/src/store/workflowStore.ts` | **可能**（本地未提交 +9 行 vs 远端 +106 行，且双方都加了 `reasoning`/`t1_us`/`dataset_id` 等字段） | **S0 先提交本地改动**；合并时以远端为骨架，手工保留本地新增字段（`reasoning`、`t1_us`、`decay_visible`、`description`、`qubit`、`dataset_id`、`experiment_type`、`dataset_name`）——其中 `reasoning`/`t1_us` 等远端**同名也有**，需逐字段核对去重，避免 TS 重复属性报错 |
| 其余 262 文件 | 无冲突（`merge-tree` 已验证） | 自动合并 |

---

## 5. 明确假设

| 编号 | 假设 | 若不成立 |
|---|---|---|
| **A1** | `analysis_service.retry.max_attempts` 恢复为 5（不照合远端 30） | 改回照合，一行 |
| **A2** | 本机 Python = `C:\Users\lqcs\Programs\Python\Python311\python.exe`（两个文档路径均不存在） | 用用户指定的解释器 |
| **A3** | 本地 `session.json` 的 `20260324` 才是本机真机会话 | 改为 S3 中指定的路径 |
| **A4** | 用户接受「设计文档缺失」状态（论文未发表，不入库） | 由用户提供文件则 `git add -f` |
| **A5** | 远端 `.gitignore` 的「文档只留 3 篇」策略适用于对方的公开仓库，**不适用于本地仓库**（本地有 17 篇在跟踪的 docs + skills 体系 + 约定文件） | 若要完全照搬远端策略，则本地 `docs/` 与 `skills/` 需出库 —— 影响面大，需你明确指示 |

---

## 6. 人工验收清单（S8，由人起服务执行；Express :3002 / Next.js :8081）

**起服务**（约定 3，人执行）：
```powershell
# 参考远端提供的脚本
.\start-qmclaw.ps1        # 或 deploy-local.ps1 / scripts/start_group.py
```

**验收项**

| # | 项 | 期望 |
|---|---|---|
| 1 | `GET /api/health` 全绿 | 6 个微服务 healthy，quantum/analysis 状态 `ok` |
| 2 | **写闸门仍关闭**（红线） | `POST :3003/execute {"code":"print(1)"}` → `status=read_only`，不进入模拟分支 |
| 3 | `POST :3003/qubit/set_params` | 同样 `read_only` |
| 4 | `GET /api/workflow/qwf_examples`（也可 POST） | 返回 5 个 `.qwf` 示例（上游 proxy 只暴露了 POST，本轮补了 GET；`?includeText=1` 一并带上文本） |
| 5 | `POST /api/workflow/parse_qwf`（用 `qwf_examples/01_iqraw_contrast.qwf` 文本） | 返回规范化 IR，无 error |
| 6 | `POST /api/workflow/validate` | `diags` 无 error |
| 7 | `POST /api/workflow/qwf_to_canvas` | 返回画布节点图，与前端节点类型对齐 |
| 8 | `POST /api/workflow/canvas_to_qwf`（回传上一步） | 语义等价判定通过（`ir_equivalent`） |
| 9 | `POST /api/workflow/run_ir` + `dryRun: true` | 只出计划，**不下发硬件** |
| 10 | `GET /api/tasks/lane` | 返回通道快照（谁占用/排队/租约剩余） |
| 11 | `GET /api/quantum/jobs/{id}` 轮询 | producer 语义可拿到终态 result |
| 12 | `GET /api/workflows` | 列出 `.qwf` 存档（`data/workflows/*.qwf`） |
| 13 | 前端画布 | 可打开示例 `.qwf`、编辑、保存、导出；`QueueStatusBadge` 显示 lane 状态 |
| 14 | `POST /api/tasks/lease/release` | 仅在确认持有者已死时使用（默认不测） |
| 15 | **真机回归**（`experiment_configs.json` 已照合） | `sq.spectroscopy` 显式 freq 扫描、`sq.s21`/`iqraw`/`t1` 去掉 `do_plot` 后仍出图且 DataVault 落盘正常 |
| 16 | 本地既有能力未回归 | S21 读数工具、单比特体检报告、离线 DataVault 出图 skill 三条路径仍工作 |

---

## 7. 安全与约定检查清单

| # | 检查项 | 依据 | 落点 |
|---|---|---|---|
| S-1 | 写闸门默认 `allow_execute=false` / `allow_set_params=false` | CLAUDE.md 真机写闸门 | S3 |
| S-2 | 提交前 / 推送前各扫一次密钥 | CLAUDE.md 约定 11 | S6 / S9 |
| S-3 | `.env` 不得 `git add -f` | CLAUDE.md 约定 11 | 全程（`.env` 在 `.gitignore`） |
| S-4 | 只提交本次涉及文件，禁 `git add -A`（防带入 `tsconfig.tsbuildinfo`） | CLAUDE.md 约定 11 | 每个提交前 |
| S-5 | 起停服务由人执行 | 约定 3 | S8 全部由人操作 |
| S-6 | Python 解释器 | 约定 10（路径失效，见 A2） | S6 用本机实际路径 |
| S-7 | 新增后端日志前检查 `src/index.ts` 的日志过滤器 | 约定 9 | 本次不改日志代码；若 DSL 执行需加日志，先查过滤器 |
| S-8 | 每次改动记录 `.claude/progress.md` | 约定 5 | S9 |
| S-9 | 分段提交、收尾不留半成品 | 约定 11 | S0→S9 各自独立提交 |

---

## 8. 回退方案

| 层级 | 回退点 |
|---|---|
| 单步 | 每个 S 步骤独立提交 → `git revert <hash>` |
| 整轮 | `git reset --hard backup/pre-sync-20261111-merge`（回到 `b76d407`，完全还原） |
| 远端引用 | `qmclaw-ai/sync-qmclaw-20261111` 常驻本地只读引用，随时可重新 diff |

---

## 9. 待确认（唯一一项）

**A5**：远端 `.gitignore` 的「文档只留 3 篇 / `skills/` 整体忽略 / 忽略 CLAUDE.md·AGENTS.md」是对方公开仓库的策略。本地仓库有 17 篇在跟踪的 `docs/`、9 个 `skills/`、三个约定文件。计划按 **A5 = 不照搬**（改为放行本地路径的窄规则）执行。
→ 若你希望**完全照搬**远端策略（本地 docs/skills 出库），请说明——那会额外产生一次大规模出库提交。

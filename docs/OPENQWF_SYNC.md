# OpenQWF 同步与门面规则（DSL 独立仓融合）

> 登记日期：2026-11-19 ｜ 关联：`.github/workflows/checks.yml`（`openqwf-drift` 任务）、
> `tools/sync_openqwf.py`、`tools/check_openqwf_drift.py`、
> `vendor/openqwf/UPSTREAM.json`

## 一句话

**DSL 的实现住在独立仓 `Secsix/OpenQWF`；本仓（QMClaw）只保留一份逐字 vendor 副本
+ 一层兼容门面。** 门面里不允许再出现第二份 DSL 语义实现。

## 为什么这么切

| 诉求 | 做法 |
|---|---|
| OpenQWF 保持**相对独立性** | 实现全部在独立仓 `qwf/`（核心）+ `adapters/`（适配器）；本仓只 vendor，不 fork、不改写 |
| 适配本机**刚调通的 qiskit 后端** | 上游的 qiskit 适配器**只做 HTTP 转发**到本仓 `quantum_service`（:3003 `/qiskit/*`），不复制、不绕过 |
| 两边**不打架** | 单一真源 + vendor 副本 + CI 漂移检查：副本与上游不一致时 CI 会红 |

## 两个仓的分工

| | 上游 `Secsix/OpenQWF` | 本仓 QMClaw |
|---|---|---|
| `qwf/`（核心：IR / 校验 / 执行器 / 前端） | **唯一真源** | `vendor/openqwf/qwf/`（逐字副本） |
| `adapters/`（后端适配器：lqcs / qiskit） | **唯一真源** | `vendor/openqwf/adapters/`（逐字副本） |
| `services/workflow_service/dsl/` | —— | **兼容门面**（薄壳，见下） |
| 真机 / qiskit 执行 | 通过 `adapters/lqcs.py` 转发到 `:3003` | `services/quantum_service/`（**实现在这里**） |
| 词表 / 阈值 / 设备档案 | 通过 `QWF_*` 环境变量读本仓配置 | `qmclaw-server/config/` |

> 上游是**私有仓**：匿名 `git ls-remote` 会失败，本机靠 git credential helper 拉取。

## vendor 副本机制

```
vendor/openqwf/
├── qwf/            # 上游核心包（逐字）
├── adapters/       # 上游适配器包（逐字）
├── pyproject.toml
└── UPSTREAM.json   # 同步元信息：仓库 / 分支 / commit / 每个文件的 sha256 / 备注
```

- **同步**：`python tools/sync_openqwf.py --from <上游 clone 目录> --note "<备注>"`
  只搬 `qwf/` + `adapters/` + `pyproject.toml`；
  `config/`、`data/`、`.env`、`device_profile.json`、`qubitclient.json` **一律不搬**。
- **查漂移**：`python tools/check_openqwf_drift.py --from <上游 clone 目录>`
  （不给 `--from` 就自己 clone 远端；访问不到上游时返回 2 = 跳过，不算失败）。
- **CI**：`checks.yml` 的 `openqwf-drift` 任务用 `secrets.OPENQWF_TOKEN` 克隆上游逐文件比对；
  **没有 token 就跳过**（`continue-on-error: true`），不阻塞主线。
- **行尾**：vendor 副本统一 LF（`.gitattributes`：`vendor/openqwf/** text eol=lf`）。
  比对脚本本身也会做 CRLF→LF 归一，但文件本身保持 LF 更省心。

## 门面规则（硬约束）

`Agentic Workflow/qmclaw-server/services/workflow_service/dsl/` 下：

1. **除了下面这张例外表，所有 `.py` 都必须是一层薄壳**：

   ```python
   import sys
   from ._upstream import upstream

   sys.modules[__name__] = upstream("qwf.const")
   ```

   用 `sys.modules` **整体替换**（不是 `from x import *`）：这样
   `dsl.const is qwf.const` —— 模块对象、私有名字、类型身份都只有一份。

2. **例外表**（有实质逻辑，且都写清了为什么）：

   | 文件 | 为什么 |
   |---|---|
   | `_upstream.py` | 桥接层：找 vendor 目录 + 注入路径环境变量 |
   | `__init__.py` | 门面入口：跑桥接 + 再导出公开 API |
   | `validate.py` | 上游把 `qubit_arity` 收成私有名，这里补回**公开名** |
   | `llm.py` | 上游 `server_root()` 指向上游仓根，这里覆写成本地仓根 |
   | `vendor_semantics.py` | 厂商语义表的**惰性代理**（上游是唯一真源） |
   | `backends/__init__.py` | 后端注册表再导出（上游在独立顶层包 `adapters/`） |
   | `textdsl/__init__.py` | 子模块提前登记，避免模块对象分裂 |

3. **子模块必须提前登记**：`dsl/textdsl/__init__.py` 会把 `lexer/parser/printer`
   预先写进 `sys.modules`。不登记的话 `import dsl.textdsl.parser` 会按上游包的
   `__path__` **再加载一份**，`DSLSyntaxError` 就变成两个类，`except` 只接得住一边。

4. **自检**：`tests/test_openqwf_drift.py` 会盯着上面三条（薄壳、同一性、双表一致）。

## 路径与配置：三张表

上游 `qwf/paths.py` 的环境变量（本仓在 `_upstream.setup()` 里用 `setdefault` 补默认值，
**用户显式设置优先**；不设环境变量时上游行为与迁移前完全一致）：

| 环境变量 | 本仓取值 | 用途 |
|---|---|---|
| `QWF_CONFIG_DIR` | `qmclaw-server/config` | 阈值 / 设备档案 / 词表所在目录 |
| `QWF_VOCAB_PATH` | `qmclaw-server/config/experiment_configs.json` | 实验词表（13 个实验 + 双比特能力登记） |
| `QWF_DATA_DIR` | `qmclaw-server/data/workflow-runs` | 兜底落档目录（与 `server.DATA_DIR` 同一个） |

> 环境变量必须在**任何** `import qwf` 之前设好：上游 `qwf/metrics.py`、
> `qwf/autorevert.py`、`qwf/store.py` 在 import 时就会算路径。
> 门面入口 `dsl/__init__.py` 第一件事就是 `setup()` ✓。

## qiskit 通道：转发，不是复刻（红线）

```
.qwf → :3008 /workflows/run_ir → IrExecutor → build_experiment_code
     → POST :3003/execute → backend_policy → _execute_with_qiskit → qiskit_backend.run (Aer)
```

上游 `adapters/qiskit.py` **只做 HTTP 转发**（`POST /qiskit/run`、`GET /qiskit/health`、
`GET /qiskit/experiments`），目标是本仓 `quantum_service`。
**实现在本仓**（`services/quantum_service/`），上游不复制第二份。

DSL ↔ qiskit 的契约是**代码文本 + `config/experiment_configs.json`**（不是 import）：
`manifest_hash` 必须在 DSL 侧与 `services/task_queue/ledger.manifest_hash` **逐字节一致**。
动契约会让 arity 校验 / `_execute_with_qiskit` / `qiskit_experiments.get_spec` /
`qwf_sweep.py` **一起坏**。

## 本地专属功能怎么回流上游

1. 先在上游仓实现 + 提交 + 推送（上游是唯一真源）；
2. 本仓 `python tools/sync_openqwf.py --from <上游 clone> --note "<改了什么>"`；
3. `python tools/check_openqwf_drift.py --from <上游 clone>` → 必须 `[OK] vendor 与上游一致`；
4. 提交本仓（vendor 副本 + 门面对照改动一起）。

如果改动**只在本地、暂时不能推上游**：在 `vendor/openqwf/UPSTREAM.json` 里
把 `pending_upstream` 置 `true` —— 漂移检查会把它标成"属预期，不是漂移"，
但**必须在 backlog 里登记**（见 `docs/BACKLOG.md` N 组）。

## 常见操作

```bash
# 1) 拉上游最新（私有仓，靠 credential helper）
git clone --branch feat/qwf-multiqubit-vocab https://github.com/Secsix/OpenQWF.git <dir>

# 2) 同步进 vendor
python tools/sync_openqwf.py --from <dir> --note "说明这次同步了什么"

# 3) 查漂移（0 = 一致）
python tools/check_openqwf_drift.py --from <dir>

# 4) 回归网
python -m pytest "Agentic Workflow/qmclaw-server/tests" -q
python qwf_lint.py qwf_examples/*.qwf        # 必须 0 error
```

## 故障排查

| 症状 | 原因 / 处理 |
|---|---|
| `ModuleNotFoundError: No module named 'qwf'` | vendor 副本缺失 → 跑 `tools/sync_openqwf.py` |
| 上游找不到 `config/experiment_configs.json` | `QWF_CONFIG_DIR` / `QWF_VOCAB_PATH` 没注入 → 检查 `dsl/_upstream.py` 是否被 import |
| `except DSLSyntaxError` 有一边接不住 | 子模块被加载了两份 → 检查 `dsl/textdsl/__init__.py` 的预登记 |
| 漂移检查老报"内容不同" | 行尾不一致（CRLF）→ 确认 vendor 副本是 LF |
| `upstream` 私有仓拉不下来 | 匿名访问被拒 → 用 credential helper（`git credential`） |

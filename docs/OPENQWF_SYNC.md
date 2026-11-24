# OpenQWF 引用与门面规则（DSL 独立仓融合）

> 登记日期：2026-11-19 ｜ 2026-11-24 改为 **submodule** 引用
> 关联：`.github/workflows/checks.yml`（`openqwf-drift` 任务）、
> `tools/sync_openqwf.py`、`tools/check_openqwf_drift.py`、`.gitmodules`

## 一句话

**DSL 的实现住在独立仓 `Secsix/OpenQWF`；本仓（QMClaw）以 **git submodule** 引用它
（`vendor/OpenQWF`），本仓**不保存任何源码副本**，只记录一个指针。**
门面里不允许再出现第二份 DSL 语义实现。

## 为什么这么切

| 诉求 | 做法 |
|---|---|
| OpenQWF 保持**相对独立性** | 实现全部在独立仓 `qwf/`（核心）+ `adapters/`（适配器）；本仓只引用，不 fork、不改写 |
| 适配本机**刚调通的 qiskit 后端** | 上游的 qiskit 适配器**只做 HTTP 转发**到本仓 `quantum_service`（:3003 `/qiskit/*`），不复制、不绕过 |
| 两边**不打架** | 单一真源 + submodule 指针 + CI 漂移检查：指针落后上游时 CI 会提示 |

## 两个仓的分工

| | 上游 `Secsix/OpenQWF` | 本仓 QMClaw |
|---|---|---|
| `qwf/`（核心：IR / 校验 / 执行器 / 前端） | **唯一真源** | `vendor/OpenQWF/qwf/`（submodule，**不入库**） |
| `adapters/`（后端适配器：lqcs / qiskit） | **唯一真源** | `vendor/OpenQWF/adapters/`（submodule，**不入库**） |
| `services/workflow_service/dsl/` | —— | **兼容门面**（薄壳，见下） |
| 真机 / qiskit 执行 | 通过 `adapters/lqcs.py` 转发到 `:3003` | `services/quantum_service/`（**实现在这里**） |
| 词表 / 阈值 / 设备档案 | 通过 `QWF_*` 环境变量读本仓配置 | `server/config/` |

> 上游是**私有仓**：匿名 `git ls-remote` 会失败，本机靠 git credential helper 拉取，
> CI 靠 `secrets.OPENQWF_TOKEN`。

## submodule 机制（2026-11-24 起）

```
vendor/OpenQWF/     ← 上游仓 submodule（本仓只存一条 160000 指针，不存文件）
```

`.gitmodules`：

```
[submodule "vendor/OpenQWF"]
	path = vendor/OpenQWF
	url = https://github.com/Secsix/OpenQWF.git
	branch = main
```

- **首次拉取**（新 clone / 换机器）：
  ```bash
  git submodule update --init --recursive vendor/OpenQWF
  # 或
  python tools/sync_openqwf.py --init
  ```
- **看状态**：`python tools/sync_openqwf.py --status`
  （本仓指针 / 本地 HEAD / 上游 `main` 头，三者一目了然）。
- **更新指针**：`python tools/sync_openqwf.py --update`
  （= `git submodule update --remote`，把 submodule 拉到上游最新 `main`）。
  随后在本仓 `git add vendor/OpenQWF` 记录新指针 —— **提交与 push 都由人做**。
- **查漂移**：`python tools/check_openqwf_drift.py`
  语义是「**指针是否落后上游**」：0 = 一致，1 = 落后，2 = 无法判定（无权限 / 未初始化）。
  `--from <clone>` 用一个本地 clone 比对（无网时用，CI 也这么干）。
- **离线自检**：`python tools/sync_openqwf.py --check`
  （指针 == 本地 HEAD 且 submodule 工作区干净，不需要网络）。
- **CI**：
  - `static` 任务用 `OPENQWF_TOKEN` 拉 submodule（私有仓），**拉不到就跳过 qwf lint**；
  - `openqwf-drift` 任务克隆上游 `main` 与本仓指针比对，**没有 token 就跳过**
    （`continue-on-error: true`），不阻塞主线。
- **红线**：本仓索引里 `vendor/OpenQWF` 只能有**一条 `160000` 条目**；
  任何 `vendor/OpenQWF/**` 的普通文件都不该出现在本仓
  （`tests/test_openqwf_drift.py::test_no_source_copy_in_index` 会红）。

> 旧机制（逐字副本 + `UPSTREAM.json` 逐文件 sha256）已于 2026-11-24 移除：
> `vendor/OpenQWF/UPSTREAM.json` 不再存在，`tools/sync_openqwf.py` 与
> `tools/check_openqwf_drift.py` 已改写为 submodule 语义。

## 门面规则（硬约束）

`server/services/workflow_service/dsl/` 下：

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
   | `_upstream.py` | 桥接层：找 submodule 目录 + 注入路径环境变量 |
   | `__init__.py` | 门面入口：跑桥接 + 再导出公开 API |
   | `validate.py` | 上游把 `qubit_arity` 收成私有名，这里补回**公开名** |
   | `llm.py` | 上游 `server_root()` 指向上游仓根，这里覆写成本地仓根 |
   | `vendor_semantics.py` | 厂商语义表的**惰性代理**（上游是唯一真源） |
   | `backends/__init__.py` | 后端注册表再导出（上游在独立顶层包 `adapters/`） |
   | `textdsl/__init__.py` | 子模块提前登记，避免模块对象分裂 |

3. **子模块必须提前登记**：`dsl/textdsl/__init__.py` 会把 `lexer/parser/printer`
   预先写进 `sys.modules`。不登记的话 `import dsl.textdsl.parser` 会按上游包的
   `__path__` **再加载一份**，`DSLSyntaxError` 就变成两个类，`except` 只接得住一边。

4. **自检**：`tests/test_openqwf_drift.py` 会盯着薄壳、同一性、双表一致，
   以及「指针 == 本地 HEAD」「本仓无上游源码副本」。

## 路径与配置：三张表

上游 `qwf/paths.py` 的环境变量（本仓在 `_upstream.setup()` 里用 `setdefault` 补默认值，
**用户显式设置优先**；不设环境变量时上游行为与迁移前完全一致）：

| 环境变量 | 本仓取值 | 用途 |
|---|---|---|
| `QWF_CONFIG_DIR` | `server/config` | 阈值 / 设备档案 / 词表所在目录 |
| `QWF_VOCAB_PATH` | `server/config/experiment_configs.json` | 实验词表（13 个实验 + 双比特能力登记） |
| `QWF_DATA_DIR` | `server/data/workflow-runs` | 兜底落档目录（与 `server.DATA_DIR` 同一个） |

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

1. 先在上游仓实现 + 提交 + **推送**（上游是唯一真源；push 由人做）；
2. 本仓 `python tools/sync_openqwf.py --update`（或 submodule 里 pull 后
   `git add vendor/OpenQWF`）→ 指针随之移动；
3. `python tools/check_openqwf_drift.py` → 必须 `[OK] 指针与上游一致`；
4. 提交本仓（**只**是那一条指针 + 门面对照改动）。

上游的指针指向的 commit 必须**已经存在于 GitHub**（否则别人的 clone / CI 拉不到）：
上游有新提交却没 push 时，本仓指针会指向一个远端不存在的 commit，
`git submodule update` 会报 `not our ref`。

## 常见操作

```bash
# 1) 首次拉取 / 换机器
python tools/sync_openqwf.py --init

# 2) 看状态（指针 / 本地 HEAD / 上游 main）
python tools/sync_openqwf.py --status

# 3) 更新指针到上游最新
python tools/sync_openqwf.py --update

# 4) 查漂移（0 = 指针与上游一致）
python tools/check_openqwf_drift.py

# 5) 回归网
python -m pytest "server/tests" -q
python qwf_lint.py qwf_examples/*.qwf        # 必须 0 error
```

## 故障排查

| 症状 | 原因 / 处理 |
|---|---|
| `ModuleNotFoundError: No module named 'qwf'` | submodule 未初始化 → `git submodule update --init --recursive vendor/OpenQWF` |
| `fatal: remote error: upload-pack: not our ref` | 指针指向的 commit 还没 push 到上游 → 先推上游，再更新指针 |
| 上游找不到 `config/experiment_configs.json` | `QWF_CONFIG_DIR` / `QWF_VOCAB_PATH` 没注入 → 检查 `dsl/_upstream.py` 是否被 import |
| `except DSLSyntaxError` 有一边接不住 | 子模块被加载了两份 → 检查 `dsl/textdsl/__init__.py` 的预登记 |
| 漂移检查报「上游已前进」 | 上游有新提交 → `python tools/sync_openqwf.py --update` |
| `upstream` 私有仓拉不下来 | 匿名访问被拒 → 用 credential helper（`git credential`）或 CI 的 `OPENQWF_TOKEN` |

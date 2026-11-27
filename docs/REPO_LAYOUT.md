# 仓库根目录结构（2026-11-25 更新）

本次整理只搬家、不改行为：脚本内部路径已同步修正，`checks/run_all_checks.py`
的自检清单、CI（`.github/workflows/checks.yml`）与文档引用均已指向新位置。

## 目录职责

| 路径 | 职责 | 备注 |
|------|------|------|
| `server/` | 后端：Express :3002 + Python 微服务 | 服务启停由人控制（约定 3） |
| `server/tests/` | 全部 pytest 测试 | 原根级 `tests/` 已迁入此处（B6） |
| `web/` | 前端：Next.js :8081 | 服务启停由人控制（约定 3） |
| `checks/` | 全部自检脚本 + `run_all_checks.py` | 脚本内 `ROOT = Path(__file__).resolve().parent.parent` 指向仓库根 |
| `experiments/` | 实验产物（本机为主） | `s21_image/`、`reports/` 等；入库内容已迁出，脚本请放 `qwf_scripts/` 或 `tools/` |
| `docs/` | 设计/运维文档 | 清单与 runner 一致性由 `checks/_docs_paths_check.py` 校验 |
| `tools/` | 运维脚本与工具子目录 | OpenQWF 同步与漂移、对比度调参、比特体检客户端、照片读取 |
| `skills/` | Agent 技能（SKILL.md） | |
| `qwf_scripts/` | QWF DSL 脚本与工作流 | 被测试与文档直接引用，**留在根目录**（见下） |
| `qwf_scripts/examples/` | QWF DSL 示例 | |
| `vendor/` | 上游仓 **submodule**（只存指针） | `vendor/OpenQWF` 等，见 `docs/OPENQWF_SYNC.md` |
| `memory/` | 静态说明文件（`MEMORY.md`） | 运行期记忆在 `server/config/memory/`，**不入库** |
| `qwf_gen.py` / `qwf_run.py` / `qwf_lint.py` | QWF DSL CLI | 薄壳，委派上游 `vendor/OpenQWF/tools/`，见 `tools/README.md` |
| `reg_snapshot.py` | 寄存器快照脚本 | 被自检硬引用，**留在根目录**（见下） |
| `AGENTS.md` | 开发约定（本仓唯一约定文件） | 进展记录写 `.Codex/progress.md`（不入库） |

## 不入库的本机内容

以下路径在本机保留，但**不进入版本库**（见 `.gitignore`）：

- `_local/`（本机专属区整体不入库）
  - `_local/docs/` —— 私有设计文档、计划、评审记录
  - `_local/scripts/` —— 一次性脚本与排查工具（`checks/` `root/` `deploy_diff_tools/`
    `merge_verify_tools/`）
  - `_local/legacy/` —— 历史目录（`new_ctrl` / `new_templates` / `measure_scripts` 等）
  - `_local/artifacts/` —— 过程产物（自检归档、`be2_work/`、`qwf_out/` 等）
  - `_local/startup/` —— 本机启动脚本（`start-qmclaw.ps1`、`deploy-local.ps1`、`启动命令.txt`）
- `server/data/`、`server/logs/`、`server/dist/`
- `server/config/memory/`、`server/config/*.local.json`
- `server/config/device_profile.json`（真机标定值；仓里只有 `device_profile.example.json` 模板，
  由 `checks/_gen_device_profile.py` 在本机生成）
- `.Codex/`（开发进展与临时脚本）
- `cache/`、`logs/`、`__pycache__/`、`.pytest_cache/`、`.tmp_state_pptx/` 等构建与运行产物
- 运行时状态：`state.db`（+ `-shm` / `-wal`）、`sessions/`、`memories/`、`memory/`、`models/`、
  `images/`、`hooks/`、`cron/`、`pairing/`、`audio_cache/`、`image_cache/`、`.memsearch/`、
  `.dsh-orders/`、`SOUL.md`、`auth.lock`（Hermes 的 `HERMES_HOME` = 仓库根，这些留在仓根，
  不要搬进 `_local/`）

## 新增文件放哪里

- **自检脚本** → `checks/`；同时登记进 `checks/run_all_checks.py` 的 `OFFLINE`/`ONLINE`
  清单，并在 `docs/OPERATIONS.md` §4 表格补一行（否则 `_docs_paths_check` 报 FAIL）。
- **测试** → `server/tests/`（pytest）。
- **实验/标定脚本与产物** → `experiments/`。
- **运维工具** → `tools/`（`tools/` 下每个子目录一个工具）。
- **文档** → `docs/`；只有经用户确认的约定才写进 `AGENTS.md`。
- **一次性排查脚本**：确认没价值就删，不要长期堆在根目录或 `checks/`。

## 为什么 `qwf_scripts/` / `reg_snapshot.py` 还在根目录

它们被代码按 `REPO_ROOT / "..."` 硬引用：

- `qwf_scripts/` ← `server/tests/test_qwf_scripts.py`
- `reg_snapshot.py` ← `checks/_d2_registry_offline_check.py`、`checks/_registry_ep_check.py`

搬走要么改测试、要么改自检的硬路径，收益不大，故保持原位。

# 仓库根目录结构（2026-11-20 整理）

本次整理只搬家、不改行为：脚本内部路径已同步修正，`checks/run_all_checks.py`
的自检清单、CI（`.github/workflows/checks.yml`）与文档引用均已指向新位置。

## 目录职责

| 路径 | 职责 | 备注 |
|------|------|------|
| `server/` | 后端：Express :3002 + Python 微服务 | 服务启停由人控制（约定 3） |
| `web/` | 前端：Next.js :8081 | 服务启停由人控制（约定 3） |
| `checks/` | 全部自检脚本 + `run_all_checks.py` | 脚本内 `ROOT = Path(__file__).resolve().parent.parent` 指向仓库根 |
| `experiments/` | 实验脚本、数据与产物 | `自动化单比特标定/`、`s21_image/`、`reports/`、`t1_fit.py` |
| `legacy/` | 历史目录，仅存档 | 已在 `.gitignore` 中忽略，内容不进版本库 |
| `docs/` | 设计/运维文档 | 清单与 runner 一致性由 `checks/_docs_paths_check.py` 校验 |
| `tools/` | 运维脚本 | OpenQWF 同步与漂移、比特体检、照片读取、单比特自动标定 |
| `skills/` | Agent 技能（SKILL.md） | |
| `tests/` | 仓库级测试 | 服务内测试在 `server/tests/` |
| `vendor/` | 上游仓副本 | `vendor/openqwf/` 是真 clone，同步规则见 `docs/OPENQWF_SYNC.md` |
| `qwf_*.py` | QWF DSL CLI | `qwf_scripts/`、`qwf_examples/` 被测试与文档直接引用，**留在根目录** |

## 新增文件放哪里

- **自检脚本** → `checks/`；同时登记进 `checks/run_all_checks.py` 的 `OFFLINE`/`ONLINE`
  清单，并在 `docs/OPERATIONS.md` §4 表格补一行（否则 `_docs_paths_check` 报 FAIL）。
- **实验/标定脚本与产物** → `experiments/`。
- **运维工具** → `tools/`（`tools/` 下每个子目录一个工具）。
- **文档** → `docs/`；只有经用户确认的约定才写进 `AGENTS.md`。
- **一次性排查脚本**：确认没价值就删，不要长期堆在根目录或 `checks/`。

## 为什么 `qwf_scripts/` / `reg_snapshot.py` 还在根目录

它们被代码按 `REPO_ROOT / "..."` 硬引用：

- `qwf_scripts/` ← `server/tests/test_qwf_scripts.py`
- `reg_snapshot.py` ← `checks/_d2_registry_offline_check.py`、`checks/_registry_ep_check.py`

搬走要么改测试、要么改自检的硬路径，收益不大，故保持原位。

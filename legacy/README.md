# legacy/ —— 历史目录存档

2026-11-20 从仓库根目录移入。它们**都已在 `.gitignore` 中**（不进版本库），
保留原地内容只为回溯，新代码不要 import 这里的东西。

| 现路径 | 原路径 | 说明 |
|--------|--------|------|
| `legacy/new_ctrl/` | `new_ctrl/` | 旧版测控接口封装（`task.py` / `task_template.py` + 各实验工具）。`demo_quickstart.py`、`install.md` 的旧快速上手示例引用它，已随之失效 |
| `legacy/new_templates/` | `new_templates/` | 旧实验模板（rabi / t1 / ramsey / rb / drag …） |
| `legacy/measure_scripts/` | `measure_scripts/` | 旧测量流程（`sq_workflow`） |
| `legacy/deepagents_cli/` | `deepagents_cli/` | 旧 CLI 的 agent 默认提示词 |
| `legacy/lqcs_deploy/` | `lqcs_deploy/` | 旧部署用的 requirements 片段 |

需要恢复某一份时，直接 `git mv` 不适用（未跟踪），用普通 `mv legacy/<x> <新位置>` 即可。

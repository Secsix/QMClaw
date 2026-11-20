# checks/ —— 自检脚本

原先这 49 个 `_*.py` 散落在仓库根目录；2026-11-20 统一收进本目录，行为不变。

## 怎么跑

在**仓库根目录**执行（脚本用 `__file__` 定位仓库根，从哪跑都行，但产物落在根目录的 `_checks/`）：

```bash
python checks/run_all_checks.py              # 12 套离线自检 + 归档到 _checks/<时间戳>/
python checks/run_all_checks.py --e2e        # 再加 e2e / registry 两套
python checks/run_all_checks.py --only _p3_check
python checks/_phase19_check.py              # 单个脚本也可以直接跑
```

CI 跑的是其中三个纯静态脚本：`_syntax_check.py`、`_docs_paths_check.py`、`_phase19_check.py`
（见 `.github/workflows/checks.yml`）。

## 新增自检脚本的规矩

1. 文件放本目录，命名 `_<主题>_check.py`；
2. 加进 `checks/run_all_checks.py` 的 `OFFLINE`（不连设备）或 `ONLINE`（要起服务）清单；
3. 在 `docs/OPERATIONS.md` §4 表格补一行 —— `_docs_paths_check.py` 会双向校验这三处一致；
4. 路径一律写成 `ROOT = Path(__file__).resolve().parent.parent`（**本目录的上一级才是仓库根**）。

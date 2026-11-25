# checks/ —— 自检脚本

原先这 49 个 `_*.py` 散落在仓库根目录；2026-11-20 统一收进本目录；
2026-11-24 进一步裁剪：一次性的阶段里程碑自检（`_phase*_check.py` 等）已出库，
本地文件保留可随时跑；长期有效的守卫脚本仍入库。
2026-11-25 再出库三个一次性脚本（`_governance_check.py` / `_replay_regression.py` /
`_gen_spec_qwfs.py`）——它们绑定本机路径或需要真机 fixture，不适合对外分发。
2026-11-25 本机目录整理：这些一次性脚本与阶段自检统一归档到
`_local/scripts/checks/`（本机保留，不入库、不随仓库分发）。

## 两类脚本

| 类别 | 数量 | 含义 | 何时跑 |
|---|---|---|---|
| `OFFLINE` | 18 | 纯静态 / 离线自检，不连真机、不需要起服务 | 提交前、`python checks/run_all_checks.py` |
| `ONLINE` | 3 | 需要**服务在跑**（`_p3_e2e.py` / `_registry_ep_check.py` / `_apply_dataset_ep_check.py`） | 部署后验证链路 |

ONLINE 三项默认不跑（`run_all_checks.py` 只跑 OFFLINE）；需要时显式调用：

```bash
python checks/_p3_e2e.py                 # 账本/授权/额度/退款/落档 的 HTTP 端到端
python checks/_registry_ep_check.py      # :3003 /qubit/registry 端点
python checks/_apply_dataset_ep_check.py # :3003 /qubit/apply_dataset 端点
```

## 怎么跑

在**仓库根目录**执行（脚本用 `__file__` 定位仓库根，从哪跑都行，但产物落在根目录的 `_checks/`）：

```bash
python checks/run_all_checks.py              # 18 套离线自检 + 归档到 _checks/<时间戳>/
python checks/run_all_checks.py --e2e        # 再加 e2e / registry 两套
python checks/run_all_checks.py --only _p3_check
python checks/_web_proxy_check.py             # 单个脚本也可以直接跑
```

CI 跑的是其中四个纯静态脚本：`_syntax_check.py`、`_docs_paths_check.py`、`_web_proxy_check.py`、`_repo_hygiene_check.py`
（见 `.github/workflows/checks.yml`）。

## 新增自检脚本的规矩

1. 文件放本目录，命名 `_<主题>_check.py`；
2. 加进 `checks/run_all_checks.py` 的 `OFFLINE`（不连设备）或 `ONLINE`（要起服务）清单；
3. 在 `docs/OPERATIONS.md` §4 表格补一行 —— `_docs_paths_check.py` 会双向校验这三处一致；
4. 路径一律写成 `ROOT = Path(__file__).resolve().parent.parent`（**本目录的上一级才是仓库根**）。

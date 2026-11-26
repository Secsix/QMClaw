# 服务边界：底层测控细节住在哪里

> 一句话：**技能只写怎么判断，微服务负责怎么做到。**
> 这份文档说明边界划在哪、每条能力落在哪个端点、技能允许写到什么程度，
> 以及怎么用自检脚本守住这条线（`checks/_skills_boundary_check.py`）。

## 1. 为什么要划这条线

早期技能里写着「先 `qter.fitData(...)` 再取 `T1_us`」「用 `hdf5` 读数据文件」这类内容。三个问题：

| 问题 | 后果 |
|------|------|
| 换后端就失效 | 同一段调用只对 LQCS 成立；qiskit 与离线回放看不懂 |
| 参数写错到真机才炸 | 技能里没有参数表，只能靠记忆 |
| 结论不可审计 | 拟合口径散在技能与脚本里，同一个对比度能算出三个值 |

所以：**能适配多个后端的、需要校验参数的、会产生审计记录的，全部下沉到微服务。**

## 2. 能力落点表

| 能力 | 端点 | 谁在用 |
|------|------|--------|
| 列出可跑的实验 kind（含后端支持矩阵） | `GET /api/quantum/experiments/catalog` | 技能、智能体、`state_route` |
| 按 kind 跑一次实验（三后端统一） | `POST /api/quantum/experiments/run` | 技能、`run_experiment` 工具、`measurement_loop` |
| 比特状态 / 状态表 / 多 scope | `POST /api/quantum/qubit/state`、`GET /api/quantum/qubit/state-table?scope=` | 状态图、技能、循环 |
| 下一步该跑什么（G0–G4） | `POST /api/quantum/qubit/route` | 循环、`state_route` 节点 |
| 演练证据（人工写入的判据值） | `GET/POST /api/quantum/qubit/evidence` | 演示剧本、循环 |
| 按 kind 拟合一个数据集 | `POST /api/analysis/fit` | 技能、`analyze_dataset` 工具 |
| 指标 → 状态机证据的换算口径 | `POST /api/analysis/metrics/convert`、`GET /api/analysis/metrics/schema` | 技能、脚本 |
| 离线数据集列举 / 复现出图 | `GET /api/analysis/replay/datasets`、`POST /api/analysis/replay/plot` | 离线复现技能 |
| 采集作业与台账 | `POST /api/quantum/execute`（排障入口）、`GET /api/quantum/qubit/runs` | 运维 |

端点清单有自动生成的一份：`skills/qmclaw-service-endpoints/SKILL.md`（`python server/tools/gen_service_endpoints.py`），
所以技能里**不手抄端点表**，只说明「什么时候调哪一条」。

## 3. 技能里允许写什么、不允许写什么

允许：

- 物理判断与顺序（「对比度低先怀疑频率，不要先调 pi 幅度」）
- 判据与门限（`contrast` < 0.2 不可用 / > 0.7 良好；`t1_us` ≥ 20）
- 症状 → 先查什么（一张对照表）
- 调哪个端点、拿哪些字段、失败怎么处理
- 坑与人为约定（状态会过期、模拟后端会跳档、截图不算数据）

不允许（`checks/_skills_boundary_check.py` 会拦）：

- `import lqms` / `from qter` / `pylabrad` / `labrad_client`
- `qter.fitData(...)` / `QubitUpdater(...)`
- 数据格式细节：`hdf5`、`.pyd`
- 本机绝对路径（`D:\`、`/d/`）
- 拼接厂商调用代码（`sq.xxx(...)` 只允许作为 kind 说明出现，不作为可执行片段）

政策句（「不要直连 lqms」「这条由服务做」）是豁免的 —— 写清楚边界不违规。

## 4. 自检怎么跑

```bash
python checks/_skills_boundary_check.py      # 技能边界（禁词 + 政策句白名单）
python checks/_skills_endpoints_check.py     # 端点技能是否与代码同步
python checks/run_all_checks.py              # 全仓 20 套
```

前两个已接进 `checks/run_all_checks.py` 与 CI（`.github/workflows/checks.yml`）。
`docs/OPERATIONS.md` 的自检表格与 runner 列表保持双向一致（`_docs_paths_check.py` 会校验）。

## 5. 新能力该放哪

| 你想加的东西 | 放哪 |
|--------------|------|
| 一个新的实验 kind | `server/services/quantum_service/experiments/catalog.py` + 对应后端适配 |
| 一个新的拟合口径 | `server/services/analysis_service/fitting.py`（`FITTERS`） |
| 一个新的证据键 / 换算 | `server/services/common/metric_convert.py` |
| 一个新的状态或边 | `server/config/state_machine.json`（结构）；阈值进 `server/config/lab_criteria.json` |
| 一个新的判断经验 | 对应技能（只写判断与门限） |

## 6. 红线

- 技能的正文里不出现厂商模块名与可执行片段（政策句除外）
- 端点表不手写（自动生成，CI 校验同步）
- 拟合口径只有一份（`fitting.py`），技能与脚本都调服务
- 离线复现的结论不能当作现场实测（技能里必须标 `[离线复现]`）

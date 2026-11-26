---
name: qmclaw-offline-replay
description: >
  离线数据复现：没有真机（或真机不可用）时，用历史数据集重跑实验图与拟合，
  走 /api/analysis/replay/* 与 /api/quantum/experiments/run（backend=offline_sim）。
  说明离线数据放在哪、怎么挑数据集、复现结果与现场结果为什么可能不同。
  Triggers: '离线', 'offline', '没连上真机', '历史数据', '复现', 'replay', 'datavault',
  '回放', '离线模式', '没有数据'
version: 2.0.0
author: qmclaw
---

# 离线数据复现

目标：**没有真机也能把流程跑通、把图给学生看**，并清楚标注「这是历史数据」。

> 数据访问、格式解析、绘图全部在 `analysis_service` 里；技能只调端点，
> 不读文件、不解析格式、不 import 任何数据层库。

## 1. 先看有没有离线数据

```
GET http://localhost:3002/api/analysis/replay/datasets?qubit=q0&experiment_type=t1
```

参数都可省：`qubit`、`experiment_type`、`date`。
返回 `datasets: [{id, name, qubit, experiment_type, ...}]`；`id` 就是后续要的 `dataset_id`。

同一份清单也可以从 `/api/analysis/datasets/offline` 拿到（`/replay/datasets` 是它的语义别名）。

## 2. 复现一张图 / 一次拟合

```
POST http://localhost:3002/api/analysis/replay/plot
{"dataset_id": "20251103_00485", "kind": "t1"}
```

等于「按 `dataset_id` 取数 → 用语义拟合 → 出图」，返回 `plot_url`（`/plots/fit_*.png`）
与 `metrics` / `evidence`。

要跑完整流程（不只拟合）时用实验语义层指定离线后端：

```
POST http://localhost:3002/api/quantum/experiments/run
{"kind": "iqraw", "qubits": ["q0"], "backend": "offline_sim"}
```

- `backend` 留空或 `auto` 时按策略顺序 `lqcs → qiskit → offline_sim` 逐个试；
- **显式给 `backend` 时不会被策略偷换**；
- 离线后端找不到对应记录会如实返回 `offline_no_simulator` 或类似原因，**不假装成功**。

## 3. 离线与现场的区别（必须写进结论）

| 项 | 现场 | 离线复现 |
| --- | --- | --- |
| 数据来源 | 当次硬件 | 历史数据集 |
| 是否能推进状态 | 能（有 run_id / dataset_ids） | **不能**：不是新运行，不能当新证据 |
| 结论口径 | 当前芯片 | 当时那台芯片、当时的参数 |
| 出图 | 由本次数据生成 | 由历史数据重新拟合 |

所以离线结果只能用来：演示流程、验证分析口径、教学讲解。
**不能**用来声称「现在这个比特 T1 是多少」。

## 4. 每条结论都要带标注

写报告时固定格式：

> [离线复现] dataset `20251103_00485`（q0, t1）→ `t1_us = 42.1`（r²=0.94，scipy 引擎）

少任何一个字段，读者就无法判断这是不是现场结论。

## 5. 常见问题

- **`Offline data not available`**：离线数据目录没配好或没有索引。
  索引生成由服务侧负责（`services/common/offline_data_provider`），技能不直接跑；
  请让维护者重新生成索引。
- **`kind` 猜错**：省略 `kind` 时按数据集的 `experiment_type`；名字对不上就先显式给 `kind`。
- **拟合失败但图还在**：`/replay/plot` 里拟合失败会返回 `ok=false` 与原因，
  图可能仍生成（只有散点）—— 不要拿散点图当拟合结果。

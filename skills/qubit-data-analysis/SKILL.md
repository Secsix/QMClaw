---
name: qubit-data-analysis
description: >
  量子比特实验数据的语义分析与质量判定：用 POST /api/analysis/fit 按 kind 提取物理量，
  用 POST /api/analysis/metrics/convert 把指标换成状态机证据，再按 lab_criteria 阈值判质量。
  支持 T1、Ramsey/T2*、T2 echo、S21/扫频、XEB、IQraw 对比度、π 幅度、DRAG。
  Triggers: '数据分析', 'data analysis', '拟合', 'fitData', '实验结果分析', 'T1 怎么算',
  'T2 怎么算', '对比度', '保真度', 'qubit data', '测控数据处理'
version: 2.0.0
author: qmclaw
---

# 量子比特实验数据分析

三件事，按顺序：**取数 → 拟合 → 判质量**。每一步都有对应端点，技能里不出现模型代码。

> 拟合口径的唯一实现：`server/services/analysis_service/fitting.py`；
> 换算口径的唯一实现：`server/services/common/metric_convert.py`。
> 本文件只记「该问什么、怎么读结果、门限在哪」。

## 1. 先查能拟合什么

```
GET http://localhost:3002/api/analysis/fit/kinds
```

返回 `kinds`（kind 名）、`aliases`（别名）、`engines`（`scipy` 恒可用，`lqcs` 取决于本机）。
kind 决定用哪个模型：`t1` 指数、`ramsey*` 拉伸指数×余弦、`xeb` 幂衰减、
`iqraw`/`single_shot` 双高斯、`spectroscopy`/`s21*` 洛伦兹、`piamp`/`rabi*` 正弦、
`drag_calibrate` 抛物线。

## 2. 拟合一个数据集

```
POST http://localhost:3002/api/analysis/fit
{"dataset_id": "20251103_00485", "kind": "t1", "do_plot": true}
```

- `dataset_id` 可以是离线库索引（形如 `20251103_00485`）或活数据编号（纯数字）；
  也可以用 `x` / `y` 两列内联传数据（工具与测试用）。
- `kind` 省略时按数据集的 `experiment_type` 猜。
- `engine`：`scipy`（默认）/ `lqcs` / `auto`。**厂商引擎不可用时会明确报错**，
  不会悄悄换成另一个引擎还声称是厂商结果。
- `do_plot: true` 会返回 `plot_url`（`/plots/fit_*.png`），可直接给前端展示。

返回里值得看的是：

| 字段 | 含义 |
| --- | --- |
| `ok` / `reason` | 拟合成没成；失败时 `reason` 说明是数据太少、模型不收敛还是引擎不可用 |
| `metrics` | 物理量（`t1_us`、`t2_star_us`、`xeb_p`、`contrast`、`pi_amplitude`、`drag_delta`…） |
| `params` | 模型参数（幅度、时间常数、频率、偏置），排障时看这个 |
| `r2` | 拟合质量；`< 0.5` 会带 warning，此时结论只能当参考 |
| `evidence` | 已经换成状态机证据键的结果（可直接拿去 `/qubit/state` 的 `evidence`） |
| `warnings` | 例如「门保真度由 1 比特关系 F=1-(1-p)/2 折算」「洛伦兹未收敛，中心取极值点」 |

**不要自己再算一遍**：如果 `evidence` 里已有 `t1_us`，就不要在报告里用别的公式重算。

## 3. 判质量：门限在配置里

阈值真源是 `server/config/lab_criteria.json`（`GET /api/quantum/qubit/state` 会回带
`criteria_version`）。当前常用门限：

| 指标 | 门限 | 含义 |
| --- | --- | --- |
| `contrast` | `< 0.2` 不可用 / `0.2–0.4` 弱可用 / `> 0.4` 合格 / `> 0.7` 良好 | 态区分度 |
| `t1_us` | `≥ 20` | 能量弛豫 |
| `t2_star_us` | `≥ 1` | 相干（Ramsey） |
| `xeb_p` / `xeb_gate_fidelity` | `≥ 0.99` | 单比特门保真度 |

结论要带来源：`kind` + `dataset_id` + `run_id`。三者缺一，结论就只是猜测。

## 4. 常见坑

- **T1 的 1D 数据**：如果扫描只有时间一维而没有 zpa 维度，拟合能给出 T1，但要说明
  「本次未扫 zpa」，否则与真机上「zpa×时间」的二维结论混在一起。
- **对比度偏低先怀疑频率**：丢对比度时先看 `f10` 与 `spectroscopy` 结果，
  再用 `pidf` 修正；不要一味调 π 幅度。
- **XEB 的 p 与门保真度不是一回事**：`p` 是每周期存活率，`F=1-(1-p)/2` 是 1 比特折算；
  2 比特（CZ）要用 `cz_gate_fidelity` 判据，别拿单比特公式套。
- **拟合失败不要退成"上次的值"**：缺证据就是缺，重跑或换 kind。

## 5. 与状态机的关系

一次运行回来后：

1. `POST /api/analysis/fit`（或实验节点自动调）拿到 `metrics`；
2. `POST /api/analysis/metrics/convert` 换成 `evidence`；
3. `POST /api/quantum/qubit/evidence` 写演练证据（仅模拟/演示，`source` 如实标注）；
4. `POST /api/quantum/qubit/state` 看状态是否推进；没推进就如实报「未推进」。

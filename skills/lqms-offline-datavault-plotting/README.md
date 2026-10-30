# lqms-offline-datavault-plotting

**What it does** — 在没有 LabRAD / Registry 认证的环境里，直接读 DataVault 目录里的
`.hdf5`，重建 lqms 的 `DataLab`，调用 lqms 自带分析函数把历史数据画成实验图，
并导出 PNG / SVG / PDF。

**Version**: v1.0.0
**Status**: Stable
**Trigger Keywords**: 离线出图, DataVault, 历史数据出图, datavault, 离线绘图, hdf5 出图, 复现实验图

---

## 解决什么问题

LabRAD DataVault 里的历史数据不能直接用 lqms 出图：lqms 自带的 local vault 初始化
要读 LabRAD/Registry 认证，本机过不去；而且 `DataLab` 的内部字段（`datasets_num`、
`data_shape`、`name_info`…）在离线环境里全是空的，lqms 的 `ramsey` / `dispersive_shift` /
`IQ_raw` 会接连抛 `KeyError`。

本 skill 把「读 HDF5 → 重建 DataLab → 参数别名 → 分析 → 出图 → 矢量导出」整条链路
固化成可复用脚本，并把每一个坑写进速查表。

---

## 快速开始

```powershell
python skills\lqms-offline-datavault-plotting\scripts\plot_datavault.py `
  --dir "D:\DataVault\LQHL.dir\test.dir\20260324.dir" `
  --session "LQHL/test/20260324" `
  --qubit q3ld4 `
  --datasets 342-350 `
  --out "D:\out\plots" `
  --formats png,svg,pdf
```

输出：

```
D:\out\plots\
    342_S21_0.png / .svg / .pdf
    343_S21zpa2d_0.png / .svg / .pdf
    ...
    all_plots.pdf          ← 9 页合订本
```

---

## 能力

| 能力 | 说明 |
|------|------|
| 自动分派 | 按文件名里的实验名（S21 / T1 / IQraw / Ramsey df …）选画法 |
| 9+ 实验类型 | S21, S21zpa2d, Spectroscopy, IQraw, PiPulse, Ramsey, S21_dis, T1，未知类型兜底折线 |
| lqms 原生分析 | 拟合、找峰、IQ 分离度/SNR/可见度、chi/grr |
| 自动兜底 | lqms 入口失败时自动改自绘（单模 IQraw、2D T1 等），不中断整批 |
| 矢量导出 | SVG / PDF 纯矢量（无内嵌位图），另出 PNG 预览 |
| 合订 | 自动合并多页 PDF |
| 健壮性 | 单个数据集失败不影响其余，结束打印汇总表 |

---

## 关键约束

- **只读**：不写 LabRAD、不下发测控指令、不改原始 HDF5 与配置文件
- **不启服务**：不需要起 qmclaw 前后端
- **纯离线**：全程不连 LabRAD；参数别名 `q3ld4.* → q.*` 只在内存里加

---

## 已知限制

- `dp.spectroscopyAuto` / `dp.spectroscopy_fit` 离线不可用（依赖真实 DataLab 的
  `column_data` 等内部状态），Spectroscopy 走自绘 + `fitFunc.find_peaks`
- lqms 自带绘图的单位标注可能有误（T1 图横轴写 `ns` 实为 `μs`），引用数值以拟合结果为准
- IQraw 类图的 SVG 约 2 MB（矢量 marker 多），属正常
- `ra.IQ_raw` 只能处理 4 列双模 IQ 数据（`Is|I, Qs|I, Is|X, Qs|X`）；
  2 列单模数据（`Is|0, Qs|0`）会自动退回自绘 I/Q 散点
- `dp.T1` 只能处理一维 T1（delay 扫描）；二维 T1（zpa×delay）会自动退回
  自绘热图 + 逐 zpa 单指数拟合

---

## 相关

- `qubit-data-analysis` — 在线 `fitData` 取参与质量判定
- `quantum-experiment-plotter` — 期刊样式与学术图表规范
- `qubit-calibration` — 在线单比特标定流程

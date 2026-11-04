# qubit_health_report — DataVault 单比特体检

离线读取 LabRAD DataVault（`.hdf5`）会话数据，按比特汇总单比特性质，
按「IQraw > 0.1 且 PiAmp(Rabi) 实验正常」判活，并输出 **zpa / f10 / f21 / fread**。

不依赖 LabRAD 服务，纯离线（只需 `h5py` + `numpy` + `scipy`）。

## 用法

```powershell
python health_report.py --vault "D:\DataVault\LQHL.dir\test.dir\20260822_device1.dir" `
                        --start 3648 --end 4455 -o "..\..\reports\20260822_device1"
```

输出：

| 文件 | 内容 |
|------|------|
| `qubit_health.csv` | 每比特一行的全部指标 |
| `qubit_health.md` | 可读报告：判活结论 / 活比特参数 / 性质汇总 / 可写入 LabRAD 的参数 |

## 模块

| 文件 | 职责 |
|------|------|
| `dvread.py` | DataVault HDF5 读取：文件名解析、列提取、`Param.*`（LabRAD tagged data）解码 |
| `metrics.py` | 各实验的指标计算（IQraw / Rabi / Ramsey / 谱扫 / S21） |
| `health_report.py` | 聚合、判活、生成报告（主入口） |

## 数据约定

- 文件名：`"03649 - q21_5%c IQraw.hdf5"` → 序号 / 比特名 / 实验名。
- 寄存器快照：HDF5 属性 `Param.<名字>`，值是 `data:application/labrad;base64,...`
  的 tagged data；float64 为**大端**、偏移 11 字节。
- `Param.<比特名>.<寄存器>` 是测量时刻的寄存器快照，如 `q21_5.f10`。
- **zpa = `regs.bias_z`**（比特直流偏置），不是 `PiGate.zpa`（后者是单次 π 脉冲的 z 偏置）。

## 判活口径

1. **IQraw > 0.1**：单发 SNR `|z₁−z₀| / √((σ₀²+σ₁²)/2)`；
   同时报告归一化分离度 `|z₁−z₀|/(|z₀|+|z₁|)`。两者在本批数据上结论一致。
2. **PiAmp 实验正常**：PiPulse 的 `P1(piAmp)` 出现 Rabi 振荡——内部极大值 + 峰后回落，
   峰谷落差 ≥ 0.15 且 ≥ 4×噪声 σ（二阶差分 MAD 估计）。

## 关键实现要点

- **工作点**：本批会话是在 `(f10, zpa)` 网格上扫描的，寄存器里的 `f10` 是当时被扫描的值
  （含大量越界值，如 −201 GHz）。因此报告里的参数取**判活那条记录**的寄存器值，
  其余实验（Ramsey / 谱扫 / S21）按与工作点最近的文件对齐后再算指标。
- **Rabi 判据用模型无关法**：噪声数据的阻尼余弦拟合容易发散（对比度算出几百倍），
  因此以「内部峰 + 峰后谷 + 落差显著性」为主判据，拟合只用于给周期与最优幅度。
- **Ramsey 拟合用 FFT 主频初始化**：直接 5 参数拟合会退化到错误频率（T2* 差 3 个量级）。
  拟合不成立（r²<0.3 或振荡幅度<0.05）时报告标为「未测得」。
- **谱扫 f10 取共振峰**：P1(freq) 峰；优先选峰显著且贴近工作点 f10 的记录，
  否则取最显著的一条并标 ⚠（表示未获得干净共振）。
- **异常寄存器**：个别文件的 `fread` 被误写成 182 GHz，按会话中位数修正并标注。

## 已知限制

- `DataLab` 交互接口不可用（离线），因此未做与 LabRAD 在线数据的一致性校验。
- Ramsey 延迟窗口 0–1000 ns：T2 ≥ 1 µs 时 T2 只是下界。
- 判活阈值与 Rabi 判据可在 `health_report.py` / `metrics.py` 顶部调整。

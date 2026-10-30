---
name: lqms-offline-datavault-plotting
description: >
  离线复现 LabRAD DataVault 历史数据的实验图像。不连 LabRAD、不需要 Registry 认证，
  直接读 DataVault 目录里的 .hdf5，重建 lqms 的 DataLab 后调用 lqms 自带分析函数出图，
  并导出 PNG/SVG/PDF 矢量版。适用于历史存档数据、批量重画、换机器复现实验图。
  Triggers: '离线出图', 'DataVault', '历史数据出图', 'datavault', '离线绘图', 'hdf5 出图', '复现实验图'
version: 1.0.0
author: QMClaw Contributors
---

# 离线复现 DataVault 实验图像

在**没有 LabRAD / Registry 认证**的环境里，把已经存盘的数据集重新画成实验图像，
并沿用 lqms 自己的分析函数（拟合、找峰、IQ 分离度等），而不是只画裸折线。

> 与 `qubit-data-analysis`（在线 `qter.fitData`）、`quantum-experiment-plotter`（期刊样式）
> 的区别：本 skill 走**离线存档**路径；绘图样式规范仍复用 quantum-experiment-plotter。

---

## 适用场景

- 数据是历史存档、别人跑的，或本机 LabRAD/Registry 认证不可用
- 需要把一批数据集批量出图（一次几十个）
- 需要交矢量图（论文、PPT、Word 插图）

**不适用**：现场采数（走 `qubit-calibration`）、纯期刊样式排版（走 `quantum-experiment-plotter`）。

---

## 前置条件

| 项 | 值 |
|----|-----|
| Python | `C:\Users\lqcs\Programs\Python\Python311\python.exe`（已装 `lqms`） |
| 服务 | **不需要**起 qmclaw 前后端服务；不连 LabRAD；不下发任何测控指令 |
| 数据 | 只读；不改原始 HDF5、不改配置文件、不写真机 |

---

## 第 1 步：找到数据

LabRAD DataVault 的目录结构：

```
D:\DataVault\LQHL.dir\test.dir\20260324.dir\
    00342 - q3ld4%c S21.hdf5
    00343 - q3ld4%c S21zpa2d.hdf5
    00344 - q3ld4%c Spectroscopy.hdf5
    00350 - q3ld4%c T1.hdf5
```

| 要素 | 说明 |
|------|------|
| `<Vault>.dir/<session>.dir/<dataset>.dir/` | 三层目录，dataset 层里是 hdf5 |
| `NNNNN` | 数据集编号 = 文件名前 5 位数字，也是 `dataset_num` |
| `<qubit>%c` | LabRAD 通配转义，`%c` = 任意单字符；`q3ld4` 即量子比特名 |
| `<Experiment>` | 实验类型，**出图入口靠它分派**（S21 / T1 / IQraw …） |

---

## 第 2 步：读 HDF5

```python
import numpy as np
import lqms.data_process.local_data_vault.local_vault as lv

path = next(ROOT.glob("00342*.hdf5"))
ds = lv.dataset_loader_hdf5(str(path)[:-5])   # ← 去掉 .hdf5 后缀
arr = np.asarray(ds)                          # (N, n_ind + n_dep)
```

`ds` 上可取：`.name`、`.indeps`、`.deps`、`.parameters`。

> ⚠️ **不要**依赖 lqms 自带的 local vault 自动定位路径：它初始化时要读
> LabRAD/Registry 配置，认证失败就直接报错。直接用 `dataset_loader_hdf5` 绕开。

---

## 第 3 步：重建 DataLab（核心）

lqms 的分析函数都吃 `dc.DataLab`。离线环境要手工搭一个：

```python
from lqms.data_process import dataAnalysisCore as dc

class Header(dict):
    """表项：既能像 dict 取键（makeLabel 要 units），又要有 values() 方法。"""
    def __init__(self, name, values, **kw):
        kw.setdefault("units", kw.get("unit", ""))
        kw.setdefault("unit", kw.get("units", ""))
        super().__init__(name=name, values=values, **kw)
        self._values = values

    def values(self):
        return self._values


def build(num, root, qubit="q3ld4"):
    path = next(root.glob("%05d*.hdf5" % num))
    ds = lv.dataset_loader_hdf5(str(path)[:-5])
    arr = np.asarray(ds)
    lab = dc.DataLab.__new__(dc.DataLab)
    lab._clear_data()

    n_ind = len(ds.indeps)
    lab.inds = [Header(d.label, arr[:, i], unit=d.unit, shape=d.shape, datatype=d.datatype)
                for i, d in enumerate(ds.indeps)]
    lab.deps = [Header(d.label, arr[:, n_ind + i], unit=d.unit, legend=d.legend,
                       shape=d.shape, datatype=d.datatype)
                for i, d in enumerate(ds.deps)]

    params = dict(ds.parameters)
    for k, v in list(params.items()):          # ← 参数别名，见第 4 步
        if k.startswith(qubit + "."):
            params["q." + k[len(qubit) + 1:]] = v

    lab.data = arr
    lab.dim = n_ind
    lab.dataset_name = ds.name
    lab.dataset_num = num
    lab.datasets_num = [num]                   # 必须是列表
    lab.datasets_name = [ds.name]
    lab.data_shape = {num: arr.shape}          # 字典
    lab.grid = n_ind > 1
    lab.session = ["LQHL", "test", "20260324"]
    lab.name_info = {"name": [qubit], "q_name": qubit, "units": {}}
    lab.dv_type = "local_file"
    lab.measure_info = params
    lab.measure_info.setdefault("units", {})
    lab.parameters = params
    return lab, ds, arr
```

`dc.DataLab.__new__` + `_clear_data()` 给的模板属性：
`data / data_shape / dataset_name / dataset_num / datasets_name / datasets_num /
grid / grid_data / column_data / measure_info / name_info`，外加要自己设的 `dim`。

> `datasets_num` 传 int 而不是 list、`data_shape` 不填，都会在后续函数里炸。

---

## 第 4 步：参数别名（`q3ld4.*` → `q.*`）

HDF5 参数表里比特参数叫 `q3ld4.f10`、`q3ld4.PiGate.amp`，
而 `dp.ramsey` / `dp.dispersive_shift` / `ra.IQ_raw` 内部查的是 `q.f10`。

**在内存里补一份 `q.*` 别名**即可，不要改原始数据或配置文件：

```python
params = dict(ds.parameters)
for k, v in list(params.items()):
    if k.startswith(qubit + "."):
        params["q." + k[len(qubit) + 1:]] = v
```

---

## 第 5 步：选分析入口

| 实验 | 出图函数 | 备注 |
|------|---------|------|
| S21 | 自绘（幅值 dB + 相位，标凹点） | `dp.findDips` / `fit_Q_s21` 需要配对数据集，单条用不了 |
| S21zpa2d | `dp.zpa2d(lab, do_plot=True)` | 2D；`do_plot=False` 更稳，见第 7 步 |
| S21_dis | `dp.dispersive_shift(lab, do_plot=True)` | 返回 chi / grr |
| Spectroscopy | 自绘 + `fitFunc.find_peaks` | `dp.spectroscopyAuto/Fit` 依赖真实 DataLab 内部状态，离线喂不进去 |
| IQraw | `ra.IQ_raw(lab, do_plot=True, collect=True)` | **依赖量必须 4 列**；2 列单模会报错，见下 |
| PiPulse | 自绘 + `fitFunc.find_peaks` | `max point` 即 π 脉冲幅度 |
| Ramsey df | `dp.ramsey(lab, do_plot=True)` | 返回 T2*、条纹频率 |
| T1 | `dp.T1(lab, do_plot=True)` | **只能处理一维**（delay 扫描）；二维 zpa×delay 见下 |
| 其它 | 兜底：第一个依赖量 vs 第一个独立量 | 通用折线 |

`fitFunc.find_peaks(x, y, s=…, do_plot=False)` 返回 **dict**：

```python
{'peaks': [索引, x, y], 'dips': [索引, x, y],
 'max point': [x, y], 'min point': [x, y]}
```

### IQraw 的依赖量列数决定能不能用 lqms

`ra.IQ_raw` 假定数据是 **I/X 双模 4 列**，单模数据会直接越界：

| 依赖量 | 含义 | 处理 |
|--------|------|------|
| `Is \| I`, `Qs \| I`, `Is \| X`, `Qs \| X` | 读腔 I + X 读出双模 | `ra.IQ_raw` 正常，出分离度 / SNR / 可见度 |
| `Is \| 0` / `Qs \| 0`（或 `\| 1`） | 单模单态，只有 I/Q 两列 | lqms 报 `IndexError`，**自绘 I/Q 散点** |

先探测列数再决定：

```python
print([d["name"] for d in lab.deps])   # 4 列 -> lqms；2 列 -> 自绘
```

自绘兜底（脚本 `draw_iqraw` 已实现）：

```python
groups = {}
for i, d in enumerate(lab.deps):
    parts = [p.strip() for p in d["name"].split("|")]
    if len(parts) == 2 and parts[0].lower() in ("is", "qs"):
        groups.setdefault(parts[1], {})[parts[0].lower()] = i
# 每个 mode 一对 (Is, Qs) -> scatter
```

### T1：一维能跑，二维离线跑不了

| 数据形态 | indeps | lqms | 说明 |
|---------|--------|------|------|
| 一维 T1 | `delay` | `dp.T1` 正常 | 拟合出 T1 |
| 二维 T1 | `zpa`, `delay` | 报 `AttributeError: 'NoneType' object has no attribute 'zpa2freq'` 或 `AssertionError`（`GridData.__init__`） | 需要在线流程先跑 zpa2d 分析并填 `lab.grid_data` |

二维 T1 的自绘兜底（与 lqms 原意一致）：左图 P1(zpa, delay) 热图，右图各 zpa 处的 T1。

```python
z     = lab.inds[0].values()      # zpa
delay = lab.inds[1].values()      # delay
p1    = dep_values(lab, "P1 | I")
zu, du = np.unique(z), np.unique(delay)
# 用 searchsorted 建索引，不依赖数据行的存储顺序
grid = np.full((len(zu), len(du)), np.nan)
grid[np.searchsorted(zu, z), np.searchsorted(du, delay)] = p1

mesh = ax1.contourf(zu, du, grid.T, levels=16, cmap="jet")   # contourf 的 Z 是 (len(y), len(x))
t1s = [fit_t1(du, row) for row in grid]                       # 逐 zpa 单指数拟合
```

单指数拟合（不依赖 scipy）：对 `ln(P1)` 做一元线性回归，`T1 = -1/slope`；
`T1 > 5 × max(delay)` 判为无效（该 zpa 处基本没衰减）。

---

## 第 6 步：出图与导出

```python
import matplotlib
matplotlib.use("Agg")           # headless 必须
import matplotlib.pyplot as plt

r = dp.ramsey(lab, do_plot=True)
for i, fignum in enumerate(plt.get_fignums()):
    fig = plt.figure(fignum)
    fig.savefig(out / ("%d_Ramsey_%d.png" % (num, i)), dpi=140, bbox_inches="tight")
    fig.savefig(out / ("%d_Ramsey_%d.svg" % (num, i)), format="svg", bbox_inches="tight")
    fig.savefig(out / ("%d_Ramsey_%d.pdf" % (num, i)), format="pdf", bbox_inches="tight")
plt.close("all")
```

批量合订一本多页 PDF（可选，需要 `pypdf`）：

```python
from pypdf import PdfWriter
w = PdfWriter()
for p in sorted(out.glob("*.pdf")):
    w.append(str(p))
with open(out / "all_plots.pdf", "wb") as f:
    w.write(f)
```

---

## 第 7 步：矢量导出（纯 SVG/PDF）

**matplotlib 的 `imshow` 与 `pcolormesh` 色标条会被内嵌成位图**，SVG/PDF 不再纯矢量：

| 做法 | SVG 里是否出现 `<image>` |
|------|--------------------------|
| `imshow` + colorbar | ✅ 有（整块图变成位图） |
| `pcolormesh` + colorbar | ⚠️ 色标条仍是位图 |
| `contourf` + colorbar | ✅ 无（色标也是路径） |

**2D 图要用 `contourf`**（拟合结果仍由 lqms 给）：

```python
meta, data = dp.zpa2d(lab, do_plot=False)      # 只要拟合结果，不要自带图
zpas  = np.asarray(data[0], float).ravel()      # zpa 轴
fitf  = np.asarray(data[2], float).ravel()      # 逐 zpa 拟合出的频率
err   = np.asarray(data[3], float).ravel()      # 拟合误差 (GHz)
n_z   = len(zpas)
amp   = dep_values.reshape(amp.size // n_z, n_z)   # 频率外层 / zpa 内层
freq_axis = np.unique(indep_values)

mesh = ax1.contourf(zpas, freq_axis, amp, levels=16, cmap="jet")
ax1.plot(zpas, fitf, "k--o", ms=4, lw=1.1)
```

导出后**自检**是否纯矢量：

```powershell
$c = Get-Content fig.svg -Raw -Encoding UTF8
([regex]::Matches($c, '<image')).Count    # 期望 0
```

---

## 错误速查表

| 报错 | 原因 | 解法 |
|------|------|------|
| `KeyError: 'q_name'` | `name_info` 缺 `q_name` | `name_info={"name":[qubit],"q_name":qubit,"units":{}}` |
| `KeyError: 'q.f10'` | 参数表只有 `q3ld4.f10` | 补 `q.*` 别名（第 4 步） |
| `KeyError: 'units'` | ind/dep 表项只有 `unit` 没有 `units` | Header 同时写两个键 |
| `KeyError: 'name'` | 传了 dict 而不是带 `name` 的表项 | deps/inds 每项都要有 `name` 键 |
| `AttributeError: property 'path' has no setter` | DataLab 的 `path` 是只读属性 | 删掉对它的赋值 |
| `ValueError: too many values to unpack (expected 3)` | `spectroscopyAuto` 依赖真实 DataLab 内部状态 | 改自绘 + `find_peaks` |
| `TypeError: 'NoneType' object is unsliceable` | 同上（`spectroscopy_fit`） | 同上 |
| `TypeError: ufunc 'isfinite' not supported` | 把 `find_peaks` 的 dict 当数组用 | 取 `r['peaks'][1]` / `[2]`、`r['max point']` |
| `IndexError: arrays used as indices must be of integer type` | `IQ_raw` 遇到单模 IQ 数据（依赖量只有 2 列） | 探测 `lab.deps` 列数，2 列改自绘 I/Q 散点 |
| `AttributeError: 'NoneType' object has no attribute 'zpa2freq'` | `dp.T1` 处理二维 T1，需要先有 zpa2d 拟合结果 | 二维 T1 改自绘：热图 + 逐 zpa 指数拟合 |
| `AssertionError`（`GridData.__init__`） | 同上，离线 DataLab 没有 `grid_data` | 同上 |
| `TypeError: Length of x (N) must match number of columns in z (M)` | `contourf` 的 Z 形状应为 `(len(y), len(x))` | 传 `grid.T` |
| `ValueError: cannot reshape array of size N into shape (a, b)` | 2D 数据 reshape 维度算错 | 频率外层 / zpa 内层：`n_f = size // n_z` |
| 本地 vault 初始化认证失败 | lqms 要读 LabRAD/Registry | 用 `dataset_loader_hdf5` 直接读（第 2 步） |

---

## 已知数据陷阱

| 现象 | 说明 |
|------|------|
| T1 图横轴标 `time (ns)`，实际是 **μs** | lqms 自带绘图的单位标注问题；引用数值以拟合结果为准 |
| `WARNING:readZpa is not zero, please set f10_read...` | 数据本身 `readZpa` 非零，属预期提示，不是 bug |
| Spectroscopy 参数表 `q.f10` 与 S21_dis 反推的 `f10_read` 差几 MHz | 两者来源不同（参数表 vs 色散反推），不要混用 |
| IQraw 图 SVG 约 2 MB | 3000 点 × 多面板，全是矢量 marker，DPI 无损但文件大 |
| `find_peaks` 在基线噪声上会标出很多假峰 | 按阈值筛（`y > ymin + 0.5*(ymax-ymin)`）后再标注 |
| 同一 session 里不同数据集的依赖量结构可能不同 | 例如 IQraw 有 4 列（双模）与 2 列（单态）两种，lqms 只能处理前者 |
| T1 也有 1D / 2D 两种形态 | 2D（zpa×delay）的 lqms 入口离线不可用，脚本自动退回自绘 |
| `--qubit` 必须与数据集里的比特名一致 | 不一致时 `q.f10` 等别名缺失，依赖参数的实验会失败 |

---

## 快速脚本

`scripts/plot_datavault.py` — 自动按文件名分派实验类型、批量出图、导出三种格式：

```powershell
python scripts\plot_datavault.py `
  --dir "D:\DataVault\LQHL.dir\test.dir\20260324.dir" `
  --session "LQHL/test/20260324" `
  --qubit q3ld4 `
  --datasets 342-350 `
  --out "D:\out\plots" `
  --formats png,svg,pdf
```

| 参数 | 说明 |
|------|------|
| `--dir` | DataVault 数据集目录（装 hdf5 的那层） |
| `--session` | session 名，缺省从路径末三级推断 |
| `--qubit` | 比特名，用于参数别名（如 `q3ld4`） |
| `--datasets` | `342-350` / `342,345` / `all`（自动扫描目录里全部 hdf5） |
| `--formats` | `png,svg,pdf` 任意组合 |
| `--out` | 输出目录（自动创建） |

行为：每个数据集独立 try/except，一个失败不影响其余；结束时打印汇总表，
并在有 PDF 时自动合并成 `all_plots.pdf`。

实测：`LQHL/test/20260324` 整个 session 共 **738 个数据集全部出图成功（0 失败）**，
其中 9 个单模 IQraw 走自绘兜底、4 个二维 T1 走自绘兜底；单次全量约数分钟。

---

## 相关 skill

- `qubit-data-analysis` — 在线实验数据的 `fitData` 取参与质量判定
- `quantum-experiment-plotter` — 期刊样式（Nature/IEEE/APS）与学术图表规范
- `qubit-calibration` — 完整单比特标定流程（在线采数）
- `_shared/core/quality-standards.md` — SNR / 可见度 / T1 / T2* 质量门限

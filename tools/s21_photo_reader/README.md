# S21 照片读数工具（s21_photo_reader）

把 **S21 "find dips" 截图** 自动读成 **比特号 → 波谷值** 表格。

适用于后续陆续产生的同类照片：一次装好，以后每来一张照片跑一条命令即可。

---

## 1. 快速使用

```powershell
# 单张
python -m s21pr "D:\QMClaw\s21_image\读取线6上的待测比特.png" -o D:\QMClaw\s21_image\result

# 整目录批量（自动跳过失败，输出汇总 CSV）
python -m s21pr D:\QMClaw\s21_image\photos --summary D:\QMClaw\s21_image\photos\_summary.csv
```

输出（每张图 3 个文件）：

| 文件 | 内容 |
|---|---|
| `<名字>.csv` | 机器可读表格：比特号 / 波谷值 / 波谷幅值 / 基线 / 深度 |
| `<名字>.md` | 中文报告（主表 + 附加表 + 自检提示 + 方法说明） |
| `<名字>_annotated.png` | **核对图**：绿框=比特标签，蓝框=数值标注，红十字=识别到的波谷点 |

Windows 控制台中文乱码时先执行 `chcp 65001`，或直接看生成的 `.md` / `.csv`。

常用参数：

| 参数 | 说明 |
|---|---|
| `-o, --outdir` | 输出目录（默认与图片同级） |
| `--ocr-scale` | OCR 放大倍率，默认 2.0（小字可调 3.0） |
| `--no-annotate` | 不生成核对图 |
| `--dump-ocr` | 导出 OCR 原始 JSON（调参用） |
| `--lib-dir` / 环境变量 `S21PR_LIB_DIR` | 指定 OCR 依赖目录（默认 `<repo>\.pylibs`） |

---

## 2. 写回 LabRAD（regs.fread 赋值）

照片读数要落到 LabRAD 里的比特对象上（`q21_5.regs.fread = 6.556`）。比特对象只存在于
LabRAD 进程内，所以赋值必须**在 LabRAD 里执行**；工具负责把读数备好。

### 2.1 自动生成"粘贴即用"的赋值脚本

跑完照片识别后，工具会同时产出 `<名字>_labrad_assign.py`，内容形如：

```python
_fread_updates = {
    'q15_11': 6.2835,   # 单位 GHz
    'q21_5': 6.556,   # 单位 GHz
    ...
}
# 逐条写入 + 回读校验 + 失败单列
```

在 LabRAD 笔记里**粘贴并执行**即可（`globals()[_q]` 就是 LabRAD 里的比特设备）。

### 2.2 从 CSV 批量赋值（读数更新后不必重新生成脚本）

在 LabRAD 里执行：

```python
exec(open(r"D:\QMClaw\tools\s21_photo_reader\labrad_assign_fread.py").read())
```

脚本会读 `D:\QMClaw\s21_image\` 下最新的结果 CSV（可用环境变量 `S21PR_RESULT_CSV` 指定），
然后逐条写入 `regs.fread` 并回读校验。

也可以在 LabRAD 笔记里直接调用核心函数：

```python
import sys
sys.path.insert(0, r"D:\QMClaw\tools\s21_photo_reader")
from s21pr.assign import parse_assignments, apply_fread, format_report

rows = parse_assignments(r"D:\QMClaw\s21_image\读取线6上的待测比特.csv")
print(format_report(apply_fread(rows)))
```

### 2.3 行为约定

- **只写 `regs.fread`**，不动 `f10` / `bias_z` / `fc` 等其它寄存器；
- 每条写完**立即回读**校验（容差 1e-6），不一致的单列进"失败"清单；
- 单条失败**不中断**其它比特（LabRAD 里一个设备不在命名空间很常见）；
- 命名空间里找不到的比特会明确报"找不到比特对象 qXX_X"，而不是静默跳过；
- 支持 `dry_run=True` 只打印计划不写硬件；
- 也支持直接解析粘贴的文本：`q21_5.regs.fread = 6.556` / `q21_5 6.556` / `q21_5,6.556`。

---

## 3. 工作原理（全部自动，不含任何针对某张图的硬编码）

1. **OCR**（RapidOCR / ONNX，放大 2 倍）→ 全部文字及其坐标框。
2. **定位图框**：找贯穿绘图区的长直线 → 左/上/右/下边界。
3. **标定坐标轴**：用刻度文字（底部=频率刻度、左侧=幅值刻度）最小二乘拟合，
   并迭代剔除 OCR 读错的离群刻度。
4. **分类文本**：
   - 比特标签：`q15_11` 这类模式（`q` + 数字，可带 `_数字`）；
   - 波谷标注：**绘图区内部**的数字（matplotlib `annotate` 默认锚在数据点）；
   - 图底列表：`[6.2835, 6.353, ...]` 这类汇总文本。
5. **波谷值定值**：OCR 常把小字号数字的首位读错（`6.2835`→`5.2835`），
   因此以**几何位置**为准（图内标注锚点像素经 X 轴标定反算频率），
   并用图底列表交叉校验；两者都不一致时给出警告。
6. **曲线提取**：屏蔽文字框 + 网格线后取暗像素；每个波谷取最低点得波谷幅值，
   两侧窗口取中位数得局部基线。
   *网格线判据是"贯穿整个绘图区"——曲线里陡峭的竖直段（波谷悬崖）不会被误删。*
7. **比特 ↔ 波谷配对**：单调 DP 对齐（Needleman-Wunsch 风格），
   代价 = 归一化的横向距离 + 纵向距离；顺序保证单调，不靠"按 x 硬排"。
8. **漏识别补救**：比特标签数少于波谷数时，在各波谷附近裁剪放大 4 倍二次 OCR 补回。

工具会自检并在报告里提示：波谷数与图底列表不符、配对横向错开过大、
波谷被文字遮挡（此时按"标注文字顶边落在波谷点"的作图规则反推）等。

---

## 4. 目录结构

```
tools/s21_photo_reader/
├── README.md
├── run.ps1                 # 一键运行（自动找 Python、设 UTF-8）
├── labrad_assign_fread.py  # 在 LabRAD 里执行：CSV → regs.fread
├── s21pr/
│   ├── ocr.py              # OCR 封装
│   ├── plot.py             # 图框/坐标轴标定/文本分类
│   ├── curve.py            # 曲线提取、波谷与基线测量
│   ├── recovery.py         # 局部放大二次识别（救回漏识别的比特标签）
│   ├── pairing.py          # 比特 ↔ 波谷 单调 DP 配对
│   ├── pipeline.py         # 端到端流程
│   ├── assign.py           # 读数 → LabRAD regs.fread（纯标准库）
│   ├── report.py           # CSV / MD / 标注图输出
│   └── cli.py              # 命令行入口
└── tests/
    ├── make_synthetic.py   # 生成合成图（风格与真实截图不同）
    ├── test_tool.py        # 回归测试：合成图 → 真值比对
    └── test_assign.py      # 回归测试：读数 → fread 赋值（mock 设备）
```

---

## 5. 依赖

需要 `numpy`、`Pillow`、`rapidocr_onnxruntime`、`onnxruntime`、`opencv-python`。
仓库根的 `.pylibs` 里已装好（206 MB），工具会自动把它加入 `sys.path`。

> **解释器版本**：`.pylibs` 里的 onnxruntime 是 **cp312** 轮子，所以识别部分要 **Python 3.12**。
> 本机可用的是 DSH 运行时 `C:\Users\lqcs\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`（3.12.14）。
> CLAUDE.md 里写的 `D:\semester4\deploy\qmclaw\qmclaw\python.exe`（3.11）在本机不存在；
> 若改用它，需要把 onnxruntime/rapidocr 换成 cp311 轮子。
> **赋值部分（`s21pr/assign.py`）是纯标准库**，任何 Python 3 都能跑，也能在 LabRAD 内嵌 Python 里跑。

换机器重装（本机 pip 无法写 `.whl` 时用下载 + 解压的方式）：

```powershell
$py = "C:\Users\lqcs\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $py -m pip install --target D:\QMClaw\.pylibs rapidocr-onnxruntime onnxruntime opencv-python
```

---

## 6. 回归测试

```powershell
cd tools\s21_photo_reader\tests
python test_tool.py      # 合成图 → 真值比对
python test_assign.py   # 读数 → regs.fread 赋值（mock 设备）
```

会生成一张**合成**的 S21 图（不同频率范围、不同比特名、不同排布），
跑一遍工具并与真值比对。当前状态：**PASS**（5/5 比特名与波谷值全部还原）。
赋值测试用 mock 比特对象代替真设备，覆盖 dry-run、真写入、缺失比特、文本格式、生成脚本五个场景。

---

## 7. 已知边界

- **写入 `regs.fread` 是真机写操作**：本工具只负责生成脚本/读数，实际写入由人在 LabRAD 里执行，
  不经过 QMC 服务端的写闸门（`fallback_config.json` 的 `actuation`）。
- 图中若**没有**波谷数值标注、也没有图底列表，工具仍能给出比特列表，
  但波谷频率需靠几何反算，精度取决于 X 轴标定。
- 波谷幅值是从**栅格截图**读出来的（±5~10%），不能替代原始数据；
  波谷被文字遮挡时会按作图规则反推，报告里会标 `obscured`。
- 若换用完全不同的作图风格（手写标注、非 matplotlib 排布），
  建议先用 `--dump-ocr` 看 OCR 结果，再调 `s21pr/pairing.py` 的权重
  （`sx`/`sy` 尺度）与 `recovery.py` 的裁剪窗口。

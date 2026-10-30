---
name: s21-photo-reader
description: >-
  从 S21 "find dips" 截图自动提取「比特号 → 波谷值」表格。
  支持单张与批量目录，输出 CSV / 中文报告 / 标注核对图。
  触发词：S21 照片、读线截图、比特号、波谷值、find dips、读图、截图识别。
version: 1.0.0
author: QMClaw Contributors
---

# S21 照片读数技能

## 何时使用

用户提供 S21 测试结果**截图**（`qubit read_adcN: S21 find dips` 风格的图），
要求"识别照片内容、输出比特号与对应的 S21 波谷值"。

## 执行方式

```powershell
# 单张
D:\QMClaw\tools\s21_photo_reader\run.ps1 "<图片路径>" -OutDir "<输出目录>"

# 批量（新增照片后直接重跑）
D:\QMClaw\tools\s21_photo_reader\run.ps1 "<照片目录>" -Summary "<汇总.csv>"
```

## 第二步：写回 LabRAD（regs.fread）

识别后工具会一并生成 `<名字>_labrad_assign.py`（粘贴即用的赋值脚本）。
比特对象只存在于 LabRAD 进程内，赋值必须在 LabRAD 里执行，二选一：

```python
# A. 直接执行生成的脚本（在 LabRAD 笔记里）
exec(open(r"D:\QMClaw\s21_image\读取线6上的待测比特_labrad_assign.py").read())

# B. 从 CSV 批量赋值（读数更新后不用重新生成脚本）
exec(open(r"D:\QMClaw\tools\s21_photo_reader\labrad_assign_fread.py").read())
```

脚本只写 `regs.fread`，逐条回读校验，单条失败不影响其它比特。

## 读图要点（回答用户时务必说明）

- 图上标在比特旁边的数字是**波谷所在的频率（GHz）**——这是"波谷值"的正解；
  逐点与 X 轴刻度校验过（误差 < 1 px）。
- Y 轴是**原始读出计数**（Amplitude，0~140000），不是归一化 S21；
  工具额外给出的"波谷幅值/基线/深度"是从栅格截图读的，误差约 ±5~10%。
- 图底通常有一行汇总列表 `[6.2835, 6.353, ...]`，与图内标注互为校验。

## 结果交付

工具会生成：`<名字>.csv`、`<名字>.md`、`<名字>_annotated.png`、`<名字>_labrad_assign.py`。
回答时给用户 **Markdown 表格**（比特号 | S21 波谷值），并说明：

1. 波谷值的单位与含义（频率 GHz）；
2. 附加的幅值读数是栅格估读；
3. 报告里 `自检提示` 一节若有警告，需原样转述（例如"某波谷被标注遮挡"）。

## 校验与排错

| 现象 | 处理 |
|---|---|
| 比特数少于波谷数 | 看 `_annotated.png` 核对图；工具会自动局部放大二次 OCR 补标签 |
| 提示"配对横向错开过大" | 打开核对图确认比特与数值的对应关系，必要时人工调一次 |
| OCR 整体不准 | 加大 `--ocr-scale`（3.0），或用 `--dump-ocr` 看原始识别结果 |
| 需要改判定逻辑 | `s21pr/pairing.py`（配对权重 sx/sy）、`s21pr/recovery.py`（裁剪窗口） |

回归测试（改完逻辑必跑）：

```powershell
cd D:\QMClaw\tools\s21_photo_reader\tests; python test_tool.py
```

详见 `D:\QMClaw\tools\s21_photo_reader\README.md`。

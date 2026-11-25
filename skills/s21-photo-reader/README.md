# s21-photo-reader — S21 照片读数

> 从 S21 "find dips" **截图**自动提取「比特号 → 波谷值」表格，输出 CSV / 中文报告 / 标注核对图。

技能文件：[`SKILL.md`](SKILL.md)（version 1.0.0）。配套工具在
[`tools/s21_photo_reader/`](../../tools/s21_photo_reader/)。

## 何时用

用户丢来一张 S21 测试截图（`qubit read_adcN: S21 find dips` 风格），
要求「识别照片内容，输出比特号和对应的 S21 波谷值」。

## 怎么跑

```powershell
# 单张
.\tools\s21_photo_reader\run.ps1 "<图片路径>" -OutDir "<输出目录>"

# 批量（新增照片后重跑即可）
.\tools\s21_photo_reader\run.ps1 "<照片目录>" -Summary "<汇总.csv>"
```

或直接用 Python 模块：

```powershell
cd tools\s21_photo_reader
python -m s21pr "<图片路径>" -o "<输出目录>"
```

## 输出

| 文件 | 内容 |
|------|------|
| `<名字>.csv` | 机器可读：比特号 / 波谷值 / 波谷幅值 / 基线 / 深度 |
| `<名字>.md` | 中文报告（主表 + 附加表 + 自检提示 + 方法说明） |
| `<名字>_annotated.png` | 核对图：绿框=比特标签，蓝框=数值标注，红十字=识别到的波谷点 |

## 写回 LabRAD

比特对象只存在于 LabRAD 进程内，赋值必须在 LabRAD 里执行（二选一）：

```python
# A. 执行生成的赋值脚本（LabRAD 笔记里）
exec(open(r"<仓库根>/tools/s21_photo_reader/<名字>_labrad_assign.py").read())

# B. 从 CSV 批量赋值
exec(open(r"<仓库根>/tools/s21_photo_reader/labrad_assign_fread.py").read())
```

脚本只写 `regs.fread`，逐条回读校验，单条失败不影响其它比特。

## 读图要点

- 图上标在比特旁边的数字是**波谷所在的频率（GHz）**——这才是「波谷值」的正解；
- 坐标轴频率范围与标注位数会随图片变化，识别后**务必人工看 annotated 图核对**；
- 浅色背景 / 网格线会干扰，识别置信度低的行在报告里会单列出来。

## 依赖

`rapidocr-onnxruntime` + `onnxruntime` + `opencv-python`（安装见
[`tools/s21_photo_reader/README.md`](../../tools/s21_photo_reader/README.md)）。

## 安装技能

```bash
cp -R skills/s21-photo-reader ~/.claude/skills/   # Claude Code
cp -R skills/s21-photo-reader ~/.codex/skills/    # Codex
```

## 触发词

S21 照片、读线截图、比特号、波谷值、find dips、读图、截图识别。

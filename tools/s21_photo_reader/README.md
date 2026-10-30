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

## 2. 工作原理（全部自动，不含任何针对某张图的硬编码）

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

## 3. 目录结构

```
tools/s21_photo_reader/
├── README.md
├── run.ps1                 # 一键运行（自动找 Python、设 UTF-8）
├── s21pr/
│   ├── ocr.py              # OCR 封装
│   ├── plot.py             # 图框/坐标轴标定/文本分类
│   ├── curve.py            # 曲线提取、波谷与基线测量
│   ├── recovery.py         # 局部放大二次识别（救回漏识别的比特标签）
│   ├── pairing.py          # 比特 ↔ 波谷 单调 DP 配对
│   ├── pipeline.py         # 端到端流程
│   ├── report.py           # CSV / MD / 标注图输出
│   └── cli.py              # 命令行入口
└── tests/
    ├── make_synthetic.py   # 生成合成图（风格与真实截图不同）
    └── test_tool.py        # 回归测试：合成图 → 真值比对
```

---

## 4. 依赖

需要 `numpy`、`Pillow`、`rapidocr_onnxruntime`、`onnxruntime`、`opencv-python`。
仓库根的 `.pylibs` 里已装好（206 MB），工具会自动把它加入 `sys.path`。

换机器重装（本机 pip 无法写 `.whl` 时用下载 + 解压的方式）：

```powershell
$py = "C:\Users\lqcs\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $py -m pip install --target D:\QMClaw\.pylibs rapidocr-onnxruntime onnxruntime opencv-python
```

---

## 5. 回归测试

```powershell
cd tools\s21_photo_reader\tests
python test_tool.py
```

会生成一张**合成**的 S21 图（不同频率范围、不同比特名、不同排布），
跑一遍工具并与真值比对。当前状态：**PASS**（5/5 比特名与波谷值全部还原）。

---

## 6. 已知边界

- 图中若**没有**波谷数值标注、也没有图底列表，工具仍能给出比特列表，
  但波谷频率需靠几何反算，精度取决于 X 轴标定。
- 波谷幅值是从**栅格截图**读出来的（±5~10%），不能替代原始数据；
  波谷被文字遮挡时会按作图规则反推，报告里会标 `obscured`。
- 若换用完全不同的作图风格（手写标注、非 matplotlib 排布），
  建议先用 `--dump-ocr` 看 OCR 结果，再调 `s21pr/pairing.py` 的权重
  （`sx`/`sy` 尺度）与 `recovery.py` 的裁剪窗口。

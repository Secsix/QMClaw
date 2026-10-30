"""S21 photo reader — 从 S21 "find dips" 截图自动提取 比特号 ↔ 波谷值。

模块划分：
    ocr      : OCR 封装（RapidOCR / ONNX），返回带坐标的文本框
    plot     : 图框/坐标轴标定 + 文本分类（刻度 / 标注 / 比特标签）
    curve    : 曲线像素提取 + 波谷幅值 / 基线测量
    pairing  : 比特标签 ↔ 波谷标注 的单调配对（DP 对齐）
    report   : CSV / Markdown / 标注图 输出
"""

__version__ = "1.0.0"

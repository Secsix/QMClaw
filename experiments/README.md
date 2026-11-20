# experiments/ —— 实验脚本与产物

2026-11-20 从仓库根目录移入，内容与行为不变。

| 子路径 | 说明 |
|--------|------|
| `自动化单比特标定/` | 可移植的真机单比特标定包（`qwf_auto_calib.py` 11 节点链、zpa 相关脚本、`PORTABLE_README.md`）。便携包内部用相对路径，整包移动不受影响 |
| `s21_image/` | S21 相关图片与图像处理脚本 |
| `reports/` | 比特体检报告样例（`20260822_device1/qubit_health.{md,csv}`） |
| `t1_fit.py` / `t1_fit_q5lu8.png` | T1 拟合脚本与一次拟合输出 |

教学/演示用的 `.qwf` 范例仍在根目录 `qwf_examples/`（被 CI 的 lint 任务直接引用）。

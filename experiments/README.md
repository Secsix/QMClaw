# experiments/ —— 实验产物与历史归档

这个目录现在只放**本机过程产物**，不入库（`.gitignore` 已挡）。

| 子路径 | 说明 | 入库 |
|--------|------|------|
| `s21_image/` | S21 照片与读数表（csv / png / md） | 否 |
| `reports/` | 体检报告样例（`<session>/qubit_health.{md,csv}`） | 否 |
| `t1_fit_q5lu8.png` | 一次 T1 拟合输出 | 否 |

## 出库记录

| 原内容 | 去向 | 原因 |
|--------|------|------|
| `自动化单比特标定/`（可移植真机标定包） | 本机历史归档 `_local/legacy/` | 流程与现场数据耦合，且标定口径已下沉到服务端 |
| `t1_fit.py` | 本机历史归档 | 一次性中间脚本 |

## 现在的分工

- **标定口径**：在服务端 `server/services/analysis_service/calibration_metrics.py`，
  门限真源 `server/config/lab_criteria.json` 的 `calibration_gates` 段
- **实验执行**：一律先落成 QWF 再提交（执行层唯一入口），脚本在 `qwf_scripts/`
- **QWF 范例**：教学/演示用范例在 `qwf_scripts/examples/`（被 CI 的 lint 任务直接引用）

本目录不再放入库的脚本；要复现一段实验，请把它写成 QWF 放进 `qwf_scripts/`，
或写成一个走服务端的客户端工具放进 `tools/`。

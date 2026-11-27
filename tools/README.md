# tools/ —— 仓内运维与实验脚本

这里放**本仓自己**的脚本：跑实验、查状态、同步上游、生成报告。
凡是需要改上游实现的，请改上游仓（`Secsix/OpenQWF`），不要在这里改。

## 目录一览

| 脚本 / 目录 | 作用 | 备注 |
|------|------|------|
| `qwf_bridge.py` | **QWF 薄壳桥接层** | 仓根 `qwf_gen.py` / `qwf_run.py` / `qwf_lint.py` 靠它委派上游实现，并打本仓补丁 |
| `qwf_exp.py` | 用一条命令跑 `qwf_scripts/` 里的 qwf 流程 / 实验脚本 | 薄壳：实际生成与执行走仓根 `qwf_run.py` |
| `qwf_sweep.py` | 把 `qwf_scripts/experiments/` 里的单项实验逐个跑一遍 | 薄壳：等价于 `qwf_exp.py --all-experiments` |
| `sync_openqwf.py` | 同步上游 OpenQWF submodule 指针 | `--init` / `--update` / `--status` / `--check` |
| `check_openqwf_drift.py` | 离线查指针是否落后上游 `main` | CI 任务 `openqwf-drift` |
| `qiskit_smoke.py` | qiskit/Aer 后端冒烟 | |
| `qiskit_dsl_smoke.py` | qiskit + DSL 端到端冒烟 | |
| `iqraw_contrast_tune.py` | 读出对比度迭代调参（iqraw→piamp→pidf 循环） | 走 `POST :3003/execute`；技能 `skills/qubit-contrast-tuning/` 指向它 |
| `qubit_health_report/` | 比特健康报告生成 | 见其 `README.md` |
| `s21_photo_reader/` | S21 照片读取（实验辅助） | 见其 `README.md` |

单比特自动标定（`tools/single_qubit_auto_calibration/`）已出库到本机历史归档，
指标与门限口径留在服务端 `server/services/analysis_service/calibration_metrics.py`，
门限真源 `server/config/lab_criteria.json` 的 `calibration_gates` 段。

## QWF 薄壳：仓根三个入口

| 入口 | 上游实现 | 本仓补丁 |
|------|------|------|
| `qwf_gen.py` | `vendor/OpenQWF/tools/qwf_gen.py` | `budget.max_datasets` **恒写**（上游只在 goal 模式写；lint 的 `E_NO_BUDGET` 要求 `max_wall`/`max_writes`/`max_datasets` 三个一起声明）；额外支持 `--max-datasets` |
| `qwf_run.py` | `vendor/OpenQWF/tools/qwf_run.py` | ① `get()` 非 2xx 也读 body（上游没有 HTTPError 分支：quantum_service 无 LabRAD 时 `/health` 返 503 + 降级报告，那是**有用信息**不是故障）② 授权留底 / RunRecord 目录从 submodule 挪到本仓 `server/data/` |
| `qwf_lint.py` | `vendor/OpenQWF/tools/qwf_lint.py` | 把配置目录指向本仓 `server/config`（上游缺省会读 submodule 里的旧 config 副本） |

薄壳由 `tools/qwf_bridge.py` 统一负责：

1. **委派**：上游 `tools/` 不是 Python 包（没有 `__init__.py`），只能用
   `importlib.util.spec_from_file_location` 按文件路径加载；
2. **环境**：`sys.path` 挂上 `vendor/OpenQWF`（让 `from qwf.lint import …` 可解析），
   并设 `QWF_CONFIG_DIR` / `QWF_VOCAB_PATH` / `QWF_DATA_DIR` 指向本仓 `server/`；
3. **补丁**：见上表。

### 第一次用要先拉 submodule

```bash
git submodule update --init --recursive vendor/OpenQWF
# 或者
python tools/sync_openqwf.py --init
```

submodule 没拉全时，薄壳会直接报错并给出上面这条提示（不会静默跑旧副本）。

## 常用命令

```bash
# 生成 .qwf（budget 三件套齐全）
python qwf_gen.py --fn sq.t1 --qubit q2 --param zpa=0.0 --param delay=0,2000,100 --out t1_q2.qwf

# 静态体检（不起服务、不碰硬件）
python qwf_lint.py t1_q2.qwf
python qwf_lint.py t1_q2.qwf --json --strict

# 跑实验
python qwf_run.py t1_q2.qwf --dry        # 干跑
python qwf_run.py t1_q2.qwf --if yes     # 真跑

# 批量
python tools/qwf_exp.py --list
python tools/qwf_exp.py calib_single --dry
python tools/qwf_sweep.py --if yes --out D:/tmp/sweep
```

细则见 `docs/OPENQWF_SYNC.md`。

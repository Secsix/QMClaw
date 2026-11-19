# Qiskit 后端（量子微服务的第三个后端）

在 `quantum_service` 里增加一条**增量的** qiskit/Aer 本地模拟通道。
不改造现有后端抽象，不碰 `mode` 语义，不影响真机路径。

| 项 | 值 |
|----|-----|
| 端点 | `POST /qiskit/run`、`GET /qiskit/experiments`、`GET /qiskit/health` |
| 网关 | `POST /api/quantum/qiskit/run`、`GET /api/quantum/qiskit/experiments`、`GET /api/quantum/qiskit/health` |
| 前端 | 顶栏「⚛️ Qiskit」按钮 → `QiskitBackendPanel` |
| 依赖 | **可选**：`python/requirements-qiskit.txt`（qiskit + qiskit-aer） |
| 单测 | `python -m unittest tests.test_qiskit_backend`（28 个用例） |

---

## 1. 为什么要单独做一条通道

仓库里有**三套互不相干的 "backend" 抽象**，别搞混：

| 抽象 | 位置 | 和本后端的关​系 |
|------|------|----------------|
| `backends.BackendInterface` | `qmclaw-server/backends/` | **无关**。那是 `scripts/job_runner.py` 用的后端进程插件协议（有 register / init_backend / 任务队列 / Ray），`backends/registry.py` 里虽然预留了 `"qiskit": "QiskitBackend"`，但**类不存在**，`config/system.json` 的 `backends.qiskit.enabled` 必须保持 `false` |
| `dsl.backends.BackendAdapter` | `services/workflow_service/dsl/backends/` | **无关**。那是 workflow DSL 的后端适配器，`IMPLEMENTED = False`，S7/S8 还没接线 |
| `services/quantum_service/` | 本模块 | quantum_service 自己的 `online`/`offline`/`auto` 三态；offline 用 `offline_simulator.py`（历史 HDF5 + 合成数据，**不是**量子模拟），online 用 `labrad_client.py` → `sq.*` |

qiskit 是**进程内的本地库**，硬套 `backends/` 那套插件协议会为了一个库拖进整套
Ray 任务队列，不划算；硬塞进 `mode` 三态也会把"真机/离线"和"模拟器"搅在一起。
所以选了**增量端点**这条路：新增 `/qiskit/*`，现有端点和 `mode` 一个字都不改。

### 为什么一定要有语义映射层

`sq.*` 是**超导测控语义**（给设备下发脉冲、读 IQ），qiskit 是**门级线路模型**。
两者不是同一个东西，所以 13 个实验必须逐个声明"在 qiskit 里到底是什么"：

- `exact` —— 线路本身就是那个物理过程（T1、Ramsey、Rabi、XEB、单发读出、SWAP）
- `model` —— 用等效模型近似（谱学/S21 用 Landau-Zener 长脉冲模型，
  色散位移用两态峰位差，AllXY/DRAG 用退极化噪声 + 泄漏代理）

界面上每个实验都挂 `exact` / `model` 徽标，`/qiskit/experiments` 也返回该字段。
**`model` 的数据不能拿去当真机标定值。**

---

## 2. 单位制：无量纲

qiskit 里 **ħ = 1**，时间/频率都是无量纲的。有物理量纲的量（T1/T2/µs/Hz）靠
**入参先验**换算：传 `t1_us=50`，后端据此把无量纲的延迟映射成 µs。

所有结果统一打标：

```json
{ "backend": "qiskit", "unit_system": "qiskit_normalized" }
```

前端结果区显著显示 `unit_system`，就是防止把模拟值误当真机标定值。

---

## 3. 依赖是可选的（关键设计）

qiskit ≈ 200MB，只有用这个后端的人才需要。因此：

- qiskit **不在** `python/requirements.txt` 里，单独放 `python/requirements-qiskit.txt`
- `qiskit_experiments.py`（目录层）**刻意不 import qiskit** —— 没装 qiskit 的机器
  上服务照常启动，`/qiskit/experiments` 照常能列，只有 `/qiskit/run` 返回
  `status: "qiskit_unavailable"` + 明确的安装指引，**不是 HTTP 500**

```
services/quantum_service/
├── qiskit_experiments.py   ← 目录/数据结构/懒调度，不依赖 qiskit
├── qiskit_circuits.py      ← 13 个实验的线路构造与拟合，需要 qiskit
├── qiskit_backend.py       ← 结果信封 / 入参钳位 / 健康检查 / 执行位置选择
└── qiskit_runner.py        ← 跨解释器执行用的子进程入口
```

`run_experiment()` 用 `importlib` 懒加载 `qiskit_circuits`，ImportError 转成
`QiskitUnavailable`。

---

## 4. 跨解释器执行（本机默认走这条）

本机的两个环境互补缺失：

| 环境 | lqms（真机 LabRAD 必需） | qiskit |
|------|------------------------|--------|
| `D:\anaconda3\envs\lqcs`（服务运行环境） | ✅ | ❌ |
| `D:\anaconda3\envs\qiskit`（本任务新建） | ❌ | ✅ |

换任何一个解释器跑整个服务都会废掉另一半，所以 `qiskit_backend` 支持
**按配置选执行位置**：

```json
// config/system.json
"qiskit": {
  "enabled": true,
  "python_executable": "D:\\anaconda3\\envs\\qiskit\\python.exe",
  "shots": 2000, "seed": 42, "timeout_s": 120,
  "max_shots": 200000, "max_points": 501
}
```

- `python_executable` **留空** → 进程内执行（要求 qiskit 装在服务自己的解释器里）
- **配了别的解释器** → 起子进程，走 `qiskit_runner.py`

`/qiskit/health` 会**探测配置指定的那个解释器**（而不是当前进程），否则会出现
"子进程明明能跑，health 却说不可用"的假阴性。响应里 `execution` 字段告诉你
走的是 `in_process` 还是 `subprocess`。

### 子进程协议

```
服务进程 ──(stdin: JSON)──> qiskit 环境的 python -m services.quantum_service.qiskit_runner <tmpfile>
         <──(tmpfile: JSON)──
```

**结果走临时文件而不是 stdout**：`python -m` 会先执行包的 `__init__.py`，那一串
import 里有会打印东西的模块（实测 `MemoryStore: Initialized at ...`），stdout 上
多一行非 JSON 父进程就解析不了。走文件彻底免疫。

开销：每次请求约 +1.5s（`import qiskit + qiskit_aer` 实测 1.6s）。
没做常驻 worker 是刻意的 —— 常驻要处理存活/僵尸/崩溃重启一堆边界，
而现在的方案无状态、崩了不影响服务、**超时可以直接杀掉**（线程做不到）。
嫌慢再换。

---

## 5. 写闸门豁免

真机路径的 `allow_execute` / `allow_set_params` 闸门管的是"往实验室设备下发指令"。
**qiskit 是纯本地模拟，没有任何物理副作用，因此不受闸门约束。**

这一点在 `qiskit_backend.py` 里显式写成了 `WRITE_GATE_EXEMPTION` 常量，并由
`/qiskit/health` 返回 —— 免得以后有人看到"execute 端点没查闸门"当成漏洞。

`/qiskit/*` 也**完全不碰 LabRAD**，所以不参与 `mode`（online/offline/auto）语义。

---

## 6. 端点契约

### `POST /qiskit/run`

```jsonc
// 请求
{
  "experiment": "t1",          // 或下面二选一：传 circuit 就跑自定义线路
  "qubit": "q0",
  "params": { "t1_us": 50 },   // 各实验参数见 /qiskit/experiments
  "shots": 2000,
  "seed": 42,
  "circuit": null              // QASM2 源码（可选）
}

// 响应（与离线合成路径同构，前端/agent 不用改）
{
  "task_id": "qiskit-…",
  "status": "qiskit_completed",  // | qiskit_unavailable | qiskit_timeout | qiskit_error
  "backend": "qiskit",
  "unit_system": "qiskit_normalized",
  "experiment": "t1",
  "qubit": "q0",
  "stdout": "[QISKIT] t1 完成（exact）: 31 点, 2000 shots",
  "stderr": "",
  "error": null,
  "data": { "type": "line", "x": [...], "y": [...], "xlabel": "Delay (s)", "ylabel": "P(|1>)",
            "title": "q0: t1 (qiskit 模拟)", "grid": true },
  "metrics": { "T1": 5.06e-05 },
  "approximation": "exact",
  "elapsed_ms": 1480,
  "plotPath": "/plots/qiskit_sim_t1_….png"
}
```

`data.type` 支持 `line` / `scatter` / `bar`；`bar` 用于读出类实验和自定义线路。

### `GET /qiskit/experiments`

不需要装 qiskit 也能调。返回每个实验的：

```jsonc
{
  "name": "t1",
  "title": "T1 Relaxation",
  "approximation": "exact",          // exact | model
  "description": "…",
  "params": { "t1_us": { "default": 50.0, "unit": "us", "desc": "…" } },
  "metrics": ["T1"],                 // 与真机 experiment_configs.json 同名
  "real_function": "sq.t1",
  "available_on_hardware": true,     // 真机上能不能做
  "hardware_note": null
}
```

外加 `coverage` 段：`{real_experiments, qiskit_experiments, missing_in_qiskit,
extra_in_qiskit, covered, total}`。

### `GET /qiskit/health`

永远 200，`available` 真或假：

```jsonc
{
  "available": true,
  "enabled": true,
  "execution": "subprocess",             // in_process | subprocess
  "versions": { "qiskit": "2.6.2", "qiskit_aer": "0.18.4" },
  "python_executable": "D:\\anaconda3\\envs\\qiskit\\python.exe",
  "service_python": "D:\\anaconda3\\envs\\lqcs\\python.exe",
  "unit_system": "qiskit_normalized",
  "error": null,
  "install_hint": "pip install -r python/requirements-qiskit.txt…",
  "write_gate": "qiskit 是纯本地模拟，无物理副作用，不受真机写闸门约束。",
  "config": { "shots": 2000, "seed": 42, "timeout_s": 120, ... },
  "experiments": 13
}
```

### 网关 HTTP 码映射

`/api/quantum/qiskit/run` 按结果映射（payload 始终完整带回）：

| 后端 status | HTTP |
|-------------|------|
| `qiskit_completed` | 200 |
| `qiskit_unavailable` | 503 |
| `qiskit_timeout` | 504 |
| `qiskit_error` | 400 |
| 网关连不上微服务 | 502 |

---

## 7. 13 个实验的映射表

真机词表（`config/experiment_configs.json`）里的 13 个实验**全部覆盖**，
名字完全同名 —— agent / workflow / 前端不需要为 qiskit 另学一套命名。

| 实验 | 近似 | qiskit 里怎么做的 | 产出指标 |
|------|------|------------------|---------|
| `t1` | exact | 振幅衰减通道，扫延迟 | `T1` |
| `ramsey` | exact | H → 相位累积 → H，热弛豫通道 | `T2`, `detuning` |
| `piamp` | exact | Rabi 振荡扫幅度，取 ⟨Z⟩ 极小值定 π 脉冲 | `pi_amplitude` |
| `xeb` | exact | 随机 Clifford 序列，按**深度**拟合保真度衰减 | `gate_fidelity`, `error_per_cycle` |
| `single_shot` | exact | 制备 \|0⟩/\|1⟩ + 读出翻转通道 | `readout_fidelity` |
| `swap` | exact | 两比特 swap + Bell 基测量 | `swap_fidelity` |
| `spectroscopy` | model | 长脉冲 Landau-Zener 洛伦兹线型 | `f10` |
| `pulsed_spec` | model | 同上，观测量换成 P(\|0⟩) | `qubit_frequency` |
| `s21` | model | 比特跃迁概率代理腔透射（共振下凹） | `fread` |
| `s21_dis` | model | 两态腔曲线峰位差 = 色散位移 χ | `dispersive_shift` |
| `iqraw` | model | 单发 IQ 点云（含误判与相位抖动） | `SNR`, `F0`, `F1`, `separation` |
| `allxy` | model | 12 组门序列（理想值由无噪线路算出），退极化噪声 | `average_fidelity`, `leakage` |
| `drag_calibrate` | model | 扫 DRAG α，泄漏用第二比特旗标代理 | `optimal_drag` |

自检：`coverage_report()` 会把 qiskit 目录和真机词表对一遍，
`missing_in_qiskit` 必须为空 —— 单测 `test_covers_whole_real_machine_vocabulary`
盯着这条不变量。真机词表以后加了实验而 qiskit 没跟上，测试会红。

---

## 8. 安装与验证

```powershell
# 方式 A：装进独立 conda 环境（推荐，本机当前配置）
conda create -n qiskit python=3.11 -y --override-channels `
  -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
conda activate qiskit
pip install -r python/requirements-qiskit.txt -i https://mirrors.aliyun.com/pypi/simple/

# 方式 B：装进服务自己的解释器（省掉子进程开销）
& "D:\anaconda3\envs\lqcs\python.exe" -m pip install -r python/requirements-qiskit.txt
# 然后把 config/system.json 的 qiskit.python_executable 设为 ""
```

验证：

```powershell
cd "Agentic Workflow\qmclaw-server"
# 单测（28 个用例，含 13 个实验各一条）
& "D:\anaconda3\envs\lqcs\python.exe" -m unittest tests.test_qiskit_backend -v

# 健康检查（服务起来之后）
curl http://localhost:3002/api/quantum/qiskit/health

# 跑一个 t1
curl -X POST http://localhost:3002/api/quantum/qiskit/run `
  -H "Content-Type: application/json" `
  -d '{\"experiment\":\"t1\",\"qubit\":\"q0\",\"shots\":1000}'
```

**注意**：`conda create` 若报 `UnavailableInvalidChannel: 404 … anaconda/pkgs/pro`，
是全局 `.condarc` 里有失效的 `pkgs/pro` 频道；用上面的 `--override-channels`
指定清华源即可，**不要**去改全局 `.condarc`。

---

## 9. 已知限制

1. **QASM2 only**。`circuit` 字段走 `QuantumCircuit.from_qasm_str`；QASM3 需要额外的
   `qiskit_qasm3_import` 包（默认没装）。
2. **`model` 近似不是真物理**。谱学/S21 系列是等效模型，数值只能用来跑通流程、
   演示和做回归，不能当真机标定值。
3. **子进程每次 +1.5s**。见第 4 节，嫌慢再换常驻 worker。
4. **超时只能事后报错**。进程内路径用线程包了一层超时，但 Python 杀不掉线程，
   后台还会跑完；真正挡得住的是 `max_shots` / `max_points` 入参钳位。
   子进程路径的超时是真杀进程。
5. **qiskit 装不装得进 `lqcs` 环境未验证**。qiskit 可能要求更高的 numpy/scipy，
   而 `lqcs` 里 numpy 是 2.2.5、且有真机依赖 —— 所以本机默认走独立环境 + 子进程，
   **不动生产环境**。

---

## 10. 用 `.qwf`（工作流 DSL）跑 qiskit 实验

不经过 `/qiskit/run` 直接调，也能走完整的 qwf 链路：
`.qwf` → `parse_qwf` → `validate` → `run_ir` → 队列 → `quantum_service /execute`
→ **后端策略判定当前是 qiskit** → `_execute_with_qiskit()` 拦下 `sq.xxx(...)`
代码 → Aer 真跑一遍。

```powershell
python qwf_run.py qwf_examples\06_qiskit_t1.qwf --dry     # 干跑（不碰硬件）
python qwf_run.py qwf_examples\06_qiskit_t1.qwf --if yes  # 真跑（走 qiskit 模拟）
```

`06_qiskit_t1.qwf` 是给 qiskit 准备的最小样例（measure + say）。

### 三个必须知道的坑

1. **`read` / `analyze` 节点在 qiskit 下必然失败**。它们要读 LabRAD DataVault 的
   数据集，而 qiskit 是纯本地模拟，**不产生 DataVault 数据集**（`dataset_id` 为
   `null`）。所以 qiskit 演示流只保留 `measure`，结果直接看节点指标。
   直接跑 `qwf_examples/01_iqraw_contrast.qwf` 会看到：`c1`（measure）completed，
   `r1`（read）failed，报"实验模拟器未初始化…离线数据未就绪"。
2. **下发前的耗时闸门按真机速度算**。`dsl/backends/lqcs.py` 的耗时表是给真机的
   （`sq.t1` 不给 `zpa`/`delay` 会按厂商默认展开成 3216 点 → 估 40 分钟 →
   直接 `Halt`）。跑 qiskit 时也要显式给 `zpa` 和 `delay`，让估算落在 timeout 内。
3. **参数顺序有讲究**（历史 bug，已修）。`offline_simulator.parse_experiment_code`
   早先用 `sq\.\w+\s*\([^)]+\)` 截参数表，遇到值里带括号（`delay=range(...)`）
   就提前截断，后面的 kwargs 丢失；截下来的值还带着尾随逗号/右括号，
   `int()`/`float()` 失败 → 参数退化成字符串 → qiskit 的数值钳位又静默退回默认值。
   症状：qwf 里写 `points 21`，qiskit 实际按默认 51 点跑，**且不报错**。
   2026-11-19 已改成括号配平 + 顶层逗号切分（见 `offline_simulator.py`）。
   **服务重启后生效**（约定 3：服务启停由人控制）。


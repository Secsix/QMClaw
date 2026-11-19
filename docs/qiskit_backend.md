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

### 13 个实验全跑（2026-11-19 实测，13/13 completed）

**脚本本体在 `qwf_scripts/`**，分两层（qwf 本来就是面向工作流运行的设计）：

| 层 | 目录 | 一份脚本 | 跑法 |
|---|---|---|---|
| 工作流 | `qwf_scripts/workflows/` | 多个实验 + 门槛 + 分支 + 循环 | `python qwf_exp.py --all --if yes` |
| 单项 | `qwf_scripts/experiments/` | 一个实验（隔离失败/复测） | `python qwf_exp.py --all-experiments --if yes` |

统一入口是 `qwf_exp.py`：

```powershell
python qwf_exp.py --list                       # 看有哪些（工作流 + 单项）
python qwf_exp.py t1                           # 只打印计划（默认不跑）
python qwf_exp.py t1 --if yes                  # 真跑一个
python qwf_exp.py --all --if yes               # 全跑 4 条工作流
python qwf_exp.py --all-experiments --if yes   # 全跑 13 个单项实验
python qwf_exp.py t1 swap --if yes             # 一次跑多个（名字可混着写）
```

`qwf_sweep.py` 现在是它的薄壳（同一批脚本、同一套后端）：

```powershell
python qwf_sweep.py --if yes             # 全部 13 个单项实验
python qwf_sweep.py --if yes --only t1 ramsey   # 只跑指定的
```

每个脚本一份独立 `.qwf` 走 `qwf_run.py`（一个失败不拖累其它脚本）。
实测结果（shots=2000，seed=42，`unit_system=qiskit_normalized`）：

| 实验 | `.qwf` 里的 fn | 比特 | 类型 | 关键指标 |
|------|----------------|------|------|----------|
| T1 | `sq.t1` | 1 | exact | T1 = 4.957e-05（先验 50 µs → 拟合 49.57 µs） |
| Ramsey | `sq.ramsey_df` | 1 | exact | T2 = 2.066e-05（先验 20 µs → 拟合 20.66 µs） |
| π 幅度 | `sq.piamp` | 1 | exact | pi_amplitude = 0.2753 |
| XEB | `sq.xeb` | 1 | exact | gate_fidelity = 0.9953，error_per_cycle = 6.5e-4 |
| 单发读出 | `sq.single_shot` | 1 | exact | readout_fidelity = 0.9695（F0 0.9675 / F1 0.9715） |
| **SWAP** | `sq.swap` | **2** | exact | swap_fidelity = 0.992（counts 01:1984/2000） |
| 长脉冲谱学 | `sq.spectroscopy` | 1 | model | f10 = 0（相对驱动频率的失谐零点） |
| 脉冲谱学 | `sq.spectroscopy_iq` | 1 | model | qubit_frequency = 0（同上，相对值） |
| 腔透射 | `sq.s21` | 1 | model | fread = 0（代理，非真实腔模型） |
| 色散位移 | `sq.s21_dis` | 1 | model | dispersive_shift = 2 MHz，两条曲线中心 ±1 MHz |
| iQraw | `sq.iqraw` | 1 | model | SNR = 4.0，separation = 1.414 |
| AllXY | `sq.allxy` | 1 | model | average_fidelity = 0.9805，leakage = 0.0195 |
| **DRAG 标定** | `sq.drag_calibrate` | **2**（代理） | model | optimal_drag = 0.45，leakage_at_opt = 0 |

**双比特的表达方式**：2026-11-19 起 `.qwf` 的 `measure` 支持 `qubits` 列表
（见 §11），`swap` / `drag_calibrate` 写成 `qubits ["q0", "q1"]`，
**两个比特的顺序 = 线路里物理比特的顺序**（第 1 个 → 线路 qubit 0）。

**真机的双比特能力在别处**（2026-11-19 内省 `lqms` 得到）：不是 `sq_nodes`，
而是 `lqms.measure.tuners.cz_nodes.DCZTuner` —— **类方法**形态
（`swap11to20()` / `zpa_q_m()` / `czecho` / `xeb` …），而且要
**2 比特 + 1 耦合器**（`DCZTuner(qubits)` 里 `qubits[2]` 是 coupler）。
当前 DSL/后端链只会拼 `sq.xxx(...)` 模块级函数，**调不到它**；
登记与缺口见 `config/experiment_configs.json` 的 `vendor_capabilities.two_qubit`。

---

## 11. `.qwf` 多比特：`qubits ["q0", "q1"]`（2026-11-19）

### 写法

```
measure as c1 {
  fn      sq.swap
  qubits  ["q0", "q1"]     # 双比特
  timeout 300
}
```

* `qubit`（单数）是历史写法，等价于 `qubits ["q2ld3"]`；**两者不能同时写**
  （同时写 → `E_SCHEMA`）。
* 老 `.qwf` 一个字都不用改 —— IR v1 纯增量，旧文件照跑。

### 元数从哪来

`config/experiment_configs.json` 每条实验多了一个 `qubits` 字段
（**真机/qiskit 共用的词表**，是唯一真源）：

| 实验 | qubits |
|------|--------|
| `sq.swap` / `sq.drag_calibrate` | 2 |
| 其余 11 个 | 1 |

`dsl/experiments.py` 读这份表，`validate` 阶段比对：

* `sq.swap` 只给 1 个比特 → `E_QUBIT_ARITY`（**error**，下发前就拦下）
* `sq.t1` 给 2 个比特 → `E_QUBIT_ARITY`
* 词表里没有的厂商函数 → 不拦（不认识 ≠ 非法）

### 全链路都改了哪些地方

| 层 | 改动 |
|----|------|
| 文本 DSL | `qubits [...]` 列表字面量（词法/语法零改动，`parse_value` 本来就支持列表） |
| IR | `OP_MEASURE` 字段表加 `qubits`；`ir_version` 仍是 1 |
| 校验 | `E_QUBIT_ARITY` + `E_BAD_QUBIT`（列表元素也查） |
| 编译 | 节点 config 带 `qubits` 列表，`qubit` 保留为第一个（下游兼容） |
| 打印 | `.qwf` → IR → `.qwf` 往返不丢 `qubits` |
| 画布 | `canvas_ir` 字段表加 `qubits`（往返映射；**画布 UI 本次没做**） |
| lint | 画像检查遍历整个列表；词表里 `available=false` 的函数（如 `sq.swap`）从 `E_UNKNOWN_FN` 降级为 `W_FN_NOT_ON_HARDWARE`（真机没这函数 ≠ 写错） |
| 后端适配器 | `build_experiment_code` 拼成 `sq.swap(q0, q1, do_plot=False)` |
| quantum_service | `parse_experiment_code_multi` 解析出比特列表；请求体带 `qubits` |
| qiskit | `QiskitRequest.qubits`；双比特实验把名字映射到线路的 qubit 0/1，并写进结果 `extra.qubits` |

### 验证

```powershell
cd "Agentic Workflow\qmclaw-server"
D:\anaconda3\python.exe -m unittest tests.test_multiqubit_ir -v      # 17 项
D:\anaconda3\python.exe -m unittest tests.test_qiskit_backend -v     # 30 项
python qwf_lint.py qwf_examples\07_two_qubit_swap.qwf                # error=0
```

---

## 12. 工作流脚本：一条流程多个实验（2026-11-19）

### 为什么改

qwf 的设计目标是**面向工作流运行**（一次跑多个实验 + 门槛 + 分支 + 循环），
不是"一次跑一个实验"。原先 `qwf_scripts/` 只放了一份一个实验的脚本，
既没有流程语义，也没法表达"标定不达标就跳过下一步"这种工艺逻辑。
现在分两层：

```
qwf_scripts/workflows/    ← 主菜：一条流程多个实验
qwf_scripts/experiments/  ← 单项：隔离失败 / 单独复测
```

`qwf_exp.py --all` 跑工作流，`--all-experiments` 跑单项，`--list` 两段分开列。

### 4 条工作流

| 工作流 | 采集次数 | 内容 |
|---|---|---|
| `calib_single.qwf` | 5 | 单比特标定链：T1 → Ramsey → π 幅度 → 单发读出 → AllXY |
| `calib_two_qubit.qwf` | 1–2 | SWAP 保真度 → **达标才**标 DRAG alpha |
| `tuneup_loop.qwf` | 1–3 | 有界自主复测：`repeat max 3 stop_when T1 >= 60µs` |
| `bench_all.qwf` | 13 | 全量体检：13 个实验一条流程跑完 |

### 用的四个 DSL 机制

| 机制 | 写法 | 作用 |
|---|---|---|
| 容错 | `onError continue`（每个 measure） | 一个实验失败**不**带塌整条流程 |
| 门槛 | `assert $c1.metrics.T1 >= 0.00002 else continue as g_t1` | 比阈值，不达标记一条 FAIL（`QUALITY GATE` 行） |
| 分支 | `if $g_ro.passed { … } else { … }` | 门槛结果驱动下一步 |
| 循环 | `repeat max 3 stop_when … export c1 { … }` | 有界自主复测，硬上限 |

### 实测（2026-11-19，走完整 HTTP 链：:3008 → task_queue → :3003 → Aer）

| 工作流 | 结果 | 门槛 |
|---|---|---|
| `calib_single` | completed，42.5 s | T1 / T2 / π / 读出 / AllXY **5/5 PASS** |
| `calib_two_qubit` | completed，17.4 s | SWAP / DRAG **2/2 PASS**（分支命中，DRAG 在分支内执行） |
| `tuneup_loop` | completed，27.5 s | T1 未到 60 µs → 3 轮跑完 → **FAIL 分支**命中（提示加 shots/换 delay 区间） |
| `bench_all` | completed，107.8 s | 13 个实验全跑完，10 道门槛 **全 PASS** |

`tuneup_loop` 特意选了个**过不去**的阈值，就是为了验证"失败分支也走对了" ——
这在单实验脚本里是验证不到的。

### 三个坑（写工作流必看）

1. **别在 `say` 里插指标**：`say "…${c4.metrics.readout_fidelity}"` 在 c4 失败时
   抛 `undefined ref`，**整条流程跟着挂**（`onError continue` 只保证"继续往下走"，
   不保证"指标还在"）。指标一律经 `assert` 门槛读，再用 `$g.passed` 分支。
2. **双比特 measure 只写 `qubits`**：同时写 `qubit` 和 `qubits` 会被校验拦下
   （`E_SCHEMA measure 同时写了 qubit 和 qubits`）。`qubits` 只接受字面量，
   换比特直接改文件里的 `["q0","q1"]`。
3. **`.qwf` 阈值先写普通小数**（`0.00002` 而不是 `2e-5`）：科学计数法是上游
   `96a2240` 新加的，**要 workflow_service 重启后才生效**（服务启停由人控制）。

### 上游为此改的两处

| 上游提交 | 改了什么 | 为什么 |
|---|---|---|
| `68c9cf9` | lint 支持字符串/布尔条件（`if $c4.ok` 不再崩） | 之前 `cond.get()` 遇到字符串直接 `AttributeError` |
| `2a5af4a` | lint 的绑定属性表补 `passed` 等门槛字段 | `if $g.passed` 需要能看见门槛结果 |
| `96a2240` | `.qwf` 数字支持科学计数法；lint 绑定表补 `assert` | `2e-5` 写阈值不用再写一串 0；`assert … as g1` 的 `g1` 是真绑定，之前误报 `E_UNKNOWN_BINDING` |

上游 CI（`tools/dsl_ci.py`）在两次改动后与基线完全一致：
`selftest 258/259`、`reliability 117/130`、`usability 10/10`、`rules 48/48`、`e1verify 20/20`。



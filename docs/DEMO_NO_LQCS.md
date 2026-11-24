# 没有 lqcs 的机器上怎么跑 QMClaw（qiskit 演示模式）

> 目标：**不部署 lqcs 真机栈**的电脑（开发机、外出笔记本、CI、会议室机器），
> 也能把 QMClaw 跑起来，用 qiskit/Aer 模拟测控实验，支撑演示与日常使用。
>
> 真机机器**不受影响**：LabRAD 在，后端策略照旧走真机通道。
>
> 配套文档：真机部署与排障见 [`REAL_MACHINE.md`](REAL_MACHINE.md)，
> qiskit 后端细节见 [`qmclaw-server/docs/qiskit_backend.md`](../Agentic%20Workflow/qmclaw-server/docs/qiskit_backend.md)。
>
> 本机部署记录（`DEPLOY_LOCAL.md`）只保留在本地，不入库。

---

## 0. 一句话原理

「用不用真机」不再是一个布尔开关，而是**一份有序的后端策略**：

```
lqcs (真机)  →  qiskit (Aer 模拟)  →  offline_sim (历史数据回放)
```

唯一配置在 `qmclaw-server/config/fallback_config.json`：

```jsonc
"quantum_service": {
  "backends": {
    "order": ["lqcs", "qiskit", "offline_sim"],   // ← 唯一的开关
    "disabled": [],
    "probe_ttl_s": 30,          // lqcs 探测缓存 30 秒
    "qiskit_probe_ttl_s": 300   // qiskit 探测缓存 300 秒（可能要起子进程）
  }
}
```

- 装在真机环境里 → 第一格 `lqcs` 可用 → 走真机，**行为与改造前完全一致**
- 没装真机栈 → 第一格不可用 → 自动落到第二格 `qiskit` → 用 Aer 模拟
- 两个都不可用 → 落到第三格 `offline_sim`（历史数据回放）

原来的 `mode`（online/offline/auto）**没有删**，它现在是这份策略的兼容别名：

| mode | 实际生效的顺序 | 含义 |
|------|--------------|------|
| `online` | `["lqcs"]` | 硬指定真机。**不会**偷偷用 qiskit |
| `offline` | `["offline_sim"]` | 硬指定历史数据回放。**不会**偷偷用 qiskit |
| `auto` | 配置里的 `order` | 依次尝试，演示机自动落到 qiskit |

> `online` 模式下即使真机连不上，也**只报错、不拿模拟数据顶上** ——
> 现场拿假数据当实测比直接报错危险得多。同理，`/qubits` 在真机掉线时
> 返回空列表 + 原因，**不会**返回演示比特。

---

## 1. 环境准备（演示机）

### 1.1 Python 环境

演示机**不需要** lqms / lqcs / labrad / ray。用一份不含真机栈的依赖清单：

```powershell
conda create -n qmclaw-demo python=3.11 -y
conda activate qmclaw-demo
pip install -r "Agentic Workflow/qmclaw-server/python/requirements-demo.txt"
```

清单里只有通用依赖 + qiskit/qiskit-aer。

### 1.2 告诉量子服务用哪个解释器跑 Aer

`config/system.json` 顶层 `qiskit` 块：

```jsonc
"qiskit": {
  "python_executable": "D:\\anaconda3\\envs\\qmclaw-demo\\python.exe",
  "shots": 2000,
  "timeout_s": 60
}
```

量子服务会改走**子进程**执行（`services/quantum_service/qiskit_runner.py`），
所以量子服务本身跑在哪个 Python 里都行。

> ⚠️ 别把 `config/system.json` 里 `backends.qiskit.enabled` 打开 ——
> 那是 `scripts/job_runner.py` 的**插件槽位**，`QiskitBackend` 类并不存在，
> 改成 `true` 会让 job_runner 找不到类而失败。它必须保持 `false`。

### 1.3 Node 依赖

```powershell
cd "Agentic Workflow/qmclaw-server"; npm install
cd "..\qmclaw-web";                 npm install
```

---

## 2. 启动与自检

> **约定 3：前后端服务启停由人控制**，下面只是命令，人来敲。

```powershell
# 1) 各微服务
cd "Agentic Workflow/qmclaw-server"
& "D:\anaconda3\envs\qmclaw-demo\python.exe" -m services.quantum_service.server   # 或走 start_services.py

# 2) Express 网关 :3002
npm run dev

# 3) Next.js 前端 :8081
cd ..\qmclaw-web ; npm run dev
```

自检（任意一条都行）：

```powershell
# 后端策略报告：谁生效了、为什么别的不可用
curl http://localhost:3003/backends

# 网关代理
curl http://localhost:3002/api/quantum/backends
```

期望输出（演示机）：

```jsonc
{
  "active": "qiskit",
  "hardware": false,
  "order": ["lqcs", "qiskit", "offline_sim"],
  "backends": [
    { "name": "lqcs",       "kind": "hardware",   "available": false, "reason": "未安装 lqms（本机没有 lqcs 真机栈）" },
    { "name": "qiskit",     "kind": "simulator",  "available": true  },
    { "name": "offline_sim","kind": "replay",     "available": false, "reason": "离线数据未就绪（...）" }
  ]
}
```

---

## 3. 演示怎么走

### 3.1 前端（推荐）

打开 Next.js（:8081）→ **Experiments** 标签页：

- 比特下拉框里有 **q0 – q4**（演示比特，见 §4）
- 点 13 个实验按钮中的任意一个 → 后端用 Aer 跑 → 出图 + 指标
- 顶部徽标会显示当前后端：`⚙️ qiskit`（演示机）或 `🔌 真机`（真机）

### 3.2 命令行

```powershell
# 直接调量子服务
curl -X POST http://localhost:3003/execute `
  -H "Content-Type: application/json" `
  -d '{"code":"sq.t1(q0, do_plot=True)"}'
```

返回信封与离线合成路径同构：

```jsonc
{
  "task_id": "...",
  "status": "qiskit_completed",
  "backend": "qiskit",
  "unit_system": "qiskit_normalized",
  "experiment": "t1",
  "qubit": "q0",
  "simulated": true,
  "metrics": { "T1": 4.96e-05 },
  "data": { "type": "line", "x": [...], "y": [...] },
  "plotPath": "/plots/qiskit_sim_t1_1795050020377.png",
  "elapsed_ms": 1627
}
```

### 3.3 全部 13 个实验

| approximation | 实验 |
|---|---|
| `exact` | `t1`、`ramsey`、`piamp`、`xeb`、`single_shot`、`swap` |
| `model`  | `spectroscopy`、`pulsed_spec`、`s21`、`s21_dis`、`iqraw`、`allxy`、`drag_calibrate` |

目录与参数：`GET /api/quantum/qiskit/experiments`。

---

## 4. 演示比特 q0 – q4

演示机上没有真机、也没有历史数据时，`/qubits` 会返回 5 个**演示比特**：

- 文件：`qmclaw-server/config/demo_qubits.json`
- 返回里 `source: "demo"`，并带一句 `note` 说明数值是占位值
- 改这个文件就能换比特；把 `enabled` 设成 `false` 就彻底关掉

> 演示比特**只在「当前靠 qiskit 模拟」时**才发。真机掉线时不发假比特。

---

## 5. 运行时切换后端

```powershell
# 看看现在谁生效
curl http://localhost:3003/backends

# 临时偏好 qiskit（只影响进程内的 auto 顺序，不写回配置文件）
curl -X POST http://localhost:3003/backends -H "Content-Type: application/json" -d '{"prefer":"qiskit"}'

# 强制重探（忽略 TTL 缓存）
curl -X POST http://localhost:3003/backends -H "Content-Type: application/json" -d '{"refresh":true}'

# 清除偏好
curl -X POST http://localhost:3003/backends -H "Content-Type: application/json" -d '{"prefer":"auto"}'
```

注意：`prefer` **只对 `auto` 生效**。`online` / `offline` 是硬指定，不吃偏好。

---

## 6. 失败隔离

「任一后端连不上都不影响其他部分」的具体落地：

| 情况 | 后果 |
|---|---|
| `lqcs` 不可用 | 只影响量子服务自己；`analysis_service` 不依赖它，照常工作 |
| `qiskit` 探测/执行抛异常 | 就地转成「不可用 + 原因」，**不冒泡**；`/execute` 自动回落离线回放 |
| 探测函数抛异常 | 转成「不可用 + 原因」，探测**永不抛** |
| `lqcs` 不可用 + `mode=online` | 量子服务返回 `degraded` 报错，**不用模拟数据冒充实测** |

真机恢复时自愈：策略重新探测到 `lqcs` 可用 → 清除永久降级态 → 自动回到真机通道。

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `/backends` 里 `qiskit.available=false` | 解释器路径不对 / 没装 qiskit | 检查 `config/system.json` 的 `qiskit.python_executable`；`pip install -r python/requirements-demo.txt` |
| `/qubits` 返回空 | 既没有真机也没有演示数据 | 检查 `config/demo_qubits.json` 的 `enabled` |
| `/execute` 返回 `offline_no_simulator` | qiskit 不可用且没有离线数据 | 看 `/backends` 里 qiskit 那格的 `reason` |
| 启动日志刷屏 | 旧版本行为 | 新版探测有 TTL 缓存、只在**状态变化**时打一行；应该只看到几行 `[backend] ...` |

---

## 8. 相关文件

| 文件 | 作用 |
|---|---|
| `qmclaw-server/services/quantum_service/backend_policy.py` | 后端策略实现（顺序解析 / 探测 / 缓存 / 报告） |
| `qmclaw-server/services/quantum_service/qiskit_backend.py` | qiskit 后端（子进程调用） |
| `qmclaw-server/services/quantum_service/qiskit_experiments.py` | 13 个实验的目录与参数 |
| `qmclaw-server/config/fallback_config.json` | **唯一**的策略配置 |
| `qmclaw-server/config/demo_qubits.json` | 演示比特 |
| `qmclaw-server/python/requirements-demo.txt` | 演示机依赖（无真机栈） |
| `qmclaw-server/tests/test_backend_policy.py` | 策略与集成测试（28 例） |

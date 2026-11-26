# 测控控制台（/control）实测演示说明

> 想让「下一步做什么」由状态机决定而不是照本跑：见 [MEASUREMENT_LOOP.md](MEASUREMENT_LOOP.md)。

面向 `/control` 页面的一次"能看见状态在动"的演示：状态转移、QWF 流程图变化、
每一步实验的结果图。配套脚本 `server/tools/demo_control_console.py`。

---

## 1. 为什么需要"演练证据"通道

状态机的证据有两类来源：

| 来源 | 通道 | 说明 |
|------|------|------|
| 真实台账 | `data/run-records/*.json` → `evidence_from_records` | 只认**成功且有 `dataset_ids`** 的记录 |
| 演练证据 | `data/state_ledger/evidence.json` → `EvidenceStore` | 演示/联调用，`source=manual`，带 TTL，可一键清空 |

模拟后端（qiskit / offline_sim）产出的指标键是 `T1`(秒) / `T2` / `separation` / `SNR` /
`pi_amplitude` / `f10` / `readout_fidelity` / `gate_fidelity`，而状态机认的是
`contrast` / `t1_us` / `t2_star_us` / `xeb_p` / `xeb_gate_fidelity`；而且模拟运行**不产数据集**。
两条都不满足时，状态会永远停在 `unknown` —— 所以演示必须有一条显式的"演练证据"通道。

**边界（重要）**：

- 判定口径没有放宽：状态仍由 `services/common/qubit_state.derive_state` 按 `config/lab_criteria.json`
  的阈值计算，演练证据只是"把证据换成演示值"；
- **真实台账证据优先**：同一 key 真实有值时，演练值被丢弃，并在 `/qubit/state` 的 `conflicts` 里写清楚；
- 前端会把演练证据标出来（状态图横幅上的「演练证据 N 条（manual）」），不会被当成实测；
- 演练证据**只影响状态判定**，不参与真机写闸门（`allow_execute` / `allow_set_params` 仍是独立开关，默认关）。

---

## 2. 端点

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/qubit/evidence?name=q0` | 读该比特当前有效的演练证据（`count` / `entries` / `summary` / `path`） |
| POST | `/qubit/evidence` | 写入（`{name, entries:{...}, source, ttl_s, note, run_id}`；也可把证据键直接写在顶层） |
| POST | `/qubit/evidence/reset` | 清空（给 `name` 只清该比特，否则清全表） |

网关前缀 `/api`（Express :3002）：`/api/quantum/qubit/evidence`、`/api/quantum/qubit/evidence/reset`。

```bash
# 写入（freq_locked + contrast 走到 state_discriminated）
curl -s -X POST http://localhost:3002/api/quantum/qubit/evidence \
  -H 'Content-Type: application/json' \
  -d '{"name":"q0","entries":{"freq_locked":true,"contrast":0.85},"ttl_s":28800,"note":"手动演示"}'

# 看当前证据
curl -s -X POST http://localhost:3002/api/quantum/qubit/evidence -H 'Content-Type: application/json' -d '{"name":"q0"}'

# 清空
curl -s -X POST http://localhost:3002/api/quantum/qubit/evidence/reset -H 'Content-Type: application/json' -d '{"name":"q0"}'
```

落盘位置：`server/data/state_ledger/evidence.json`（原子写；目录不可写时降级到临时目录，
响应里的 `path` 会说实际位置）。

**键归一化**：写入时 key 一律 `strip().lower()`（`T1_us` → `t1_us`），避免大小写造出无关键。

---

## 3. 演示剧本

```bash
cd "server"

# 全流程（6 个阶段，每阶段提交一份 QWF → 等运行结束 → 把 metrics 换算成证据写入 → 打印状态转移）
python tools/demo_control_console.py --qubit q0

# 只看某一段
python tools/demo_control_console.py --stage 3

# 直接写脚本口径（QWF 不跑，或模拟后端指标对不上时）
python tools/demo_control_console.py --mode script

# 清空重来
python tools/demo_control_console.py --reset
```

| 阶段 | 实验 | 写入的证据 | 目标状态 |
|------|------|-----------|----------|
| 1 | `sq.spectroscopy` 扫频 | `freq_locked: true` | `freq_locked` |
| 2 | `sq.piamp` 幅度扫描 | `contrast: 0.30` | `amp_calibrated`（弱可用） |
| 3 | `sq.iqraw` 态区分 | `contrast: 0.85` | `state_discriminated` |
| 4 | `sq.t1` | `t1_us: 45` | `coherence_measured`（缺 T2* 时不到） |
| 5 | `sq.ramsey_df` | `t2_star_us: 32` | `coherence_measured` |
| 6 | `sq.xeb` | `xeb_p: 0.995` + `xeb_gate_fidelity: 0.995` | `characterized` |

`--mode run`（默认）先看这次运行的真实 metrics，能换算的用换算值，换算不出来才补脚本口径，
并在输出的 `notes` 里逐条写明"这个数是哪来的"（口径不做隐式转换）。

### 换算口径（`metrics_to_evidence`）

| 后端指标 | 状态机证据 | 口径 |
|----------|-----------|------|
| `contrast` | `contrast` | 直取 |
| `separation` | `contrast` | `>1` 时除以 2 归一，`<=1` 直取 |
| `SNR` | `contrast` | `SNR / 20`，截到 `[0,1]`（仅演示口径） |
| `T1` / `T2`（秒） | `t1_us` / `t2_star_us` | ×1e6 |
| `xeb_gate_fidelity` / `gate_fidelity` | `xeb_gate_fidelity` | 直取 |
| `xeb_p` / `p_xeb` | `xeb_p` | 直取 |

---

## 4. 页面上看什么

打开 <http://localhost:8081/control>（每 3 秒轮询，脚本推进后自动刷新）：

1. **① 测控状态转移图**
   - 当前态高亮；`A(s)`（当前态允许的边）用强调色加粗，其余边降透明度 —— 状态一动，高亮的边就换一批；
   - 自环（灰虚线，节点上方）/ 回退（红虚线，节点下方）/ 前向（实线）三类边；
   - 顶部横幅「状态有效 · 有效期至 …」，过期会变红并说明 GATE-TTL 会拦动作；
   - 出现「**演练证据 N 条（manual）**」chip = 现在的状态里有演示值（悬停可看键与来源）。
2. **② QWF 流程图**（@xyflow/react 只读）
   - 每次 `run_ir` 提交的 QWF 都会渲染成节点图；运行中节点闪烁；节点按状态着色；
   - 拿不到画布时（临时工作流 id）回退用运行节点链绘制，带「按运行节点回退绘制」角标。
3. **③ 实验动态**
   - 新加的「**本次运行图**」卡片：把本次运行**每个节点**产出的图（`run.nodes[].output.plotPath`）
     按节点顺序铺成缩略图，**点开看大图，Esc 关闭** —— 这是"每一步实验都出图"的地方；
   - 下面保留「当前运行中 / 上一个已结束 / 近期作业」三张老卡片（只看最后一张图）。

---

## 5. 验收命令

```bash
cd "server"
python -m unittest tests.test_evidence_store tests.test_demo_stages tests.test_quantum_evidence_endpoints -v
# 全套（注意：test_qiskit_backend 里 5 例因实验目录新增 cz.dcz 仍是 13 的旧期望，与本次改动无关）
python -m unittest discover -s tests -p "test_*.py"
```

前端类型检查：

```bash
cd "web" && npx tsc --noEmit -p tsconfig.json
```

---

## 6. 边界与红线

- 演练证据**不是**实测数据：正式结论前必须 `POST /qubit/evidence/reset` 清掉，或等 TTL 过期（默认 8 小时）；
- 不改真机写闸门默认值（`allow_execute` / `allow_set_params` 仍为 `false`）；
- 不改 `vendor/openqwf/`（DSL 上游仓）；`services/common/qubit_state.py` 的判定口径不动；
- 服务启停由人操作（:3002 Express、:3003 quantum、:3008 workflow、:8081 Next）。

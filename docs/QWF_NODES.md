# `.qwf` 节点类型覆盖实测（2026-11-19）

> 目的：`measure`（experiment）之外的节点到底能不能跑。本文只记录**实测**结果，
> 每条都标了「跑通了 / 缺件 / 未实现」，避免把"环境没配"当成"设计不支持"。
>
> 复现脚本在 `.claude/tmp/qwf_nodes/`（gitignored）。全部走 `python qwf_run.py <file> --if yes`，
> 即 HTTP 全链路：:3008 workflow_service → :3009 task_queue → :3003 quantum_service（qiskit）。

## 1. 节点全景

`vendor/openqwf/qwf/const.py` 的 `OP_TO_NODE` 决定 `.qwf` 语句编译成什么节点：

| 语句 | 节点 type | 执行方 | 实测状态 |
|------|-----------|--------|----------|
| `let` | —（不建节点，本地求值） | executor | ✅ |
| `measure` | `experiment` | quantum_service | ✅ |
| `read` | `contrast` | quantum_service 异步 job | ⚠️ 仅 lqcs+DataVault，qiskit 下不支持 |
| `analyze` | `analyze` | analysis_service :3004 | ⚠️ 同上（依赖 dataset_id） |
| `decide` | `decision` | workflow_service 直调 LLM | ✅（需 `config/qubitclient.json`） |
| `ask <question>` | `ask` | 本地 `_exec_ask` | ✅ 终态 `needs_human` |
| `ask { prompt }`（无 question） | `llm` | llm_service :3006 | ✅ |
| `vlm` | `image_analysis` | qubitclient :3010 | ✅ 2026-11-20 配好并跑通（模型/端点见 §5） |
| `classify` | `image_classification` | image_service :3007 | ⚠️ 需已训练模型 |
| `assert` | `quality_gate` | 本地 | ✅ |
| `apply` | `adjust_params` | 本地 | ✅ |
| `say "x"` | `print` | 本地 | ✅ |
| `say "x" channel notify` | `notify` | 本地 | ✅ |
| `repeat` / `if` | —（executor 本地控制流，不建节点） | executor | ✅ |
| `call` | — | 未实现（v1 P4） | ❌ 设计未做 |

## 2. 实测证据

| 探针 | 结果 |
|------|------|
| `p_ask_human.qwf` | 终态 **`needs_human`**（5.0s），节点 `q1` 带 message「这一步该用哪个标定方案？」与选项 A / B，后续 `say` 为 skipped。**这是设计行为，不是失败** |
| `p_ask_llm.qwf` | `completed`，`l1` + `s1` 均完成（10.0s）。`model minimax` 走 llm_service；写 `deepseek` 会因缺 `DEEPSEEK_API_KEY` 报错 |
| `p_control.qwf` | `completed`（35.1s）：`c1`/`ap1`/`g1` + 达标分支 `s2` 执行、`s3` skipped，`repeat` 第二轮 `c2`、`s5` 完成 |
| `p_notify.qwf` | `completed`，`notify` 与 `print` 两个节点都产出 message |
| `p_measure_decide.qwf` | `completed`，`d1` 返回 `choice="sq.ramsey_df"`（MiniMax-M2.7），`if` 据此走 `s3` 分支 ✓ |
| `p_measure_vlm.qwf` | 配置前：**假 completed**（两个子请求 500 被吞）→ 修语义后 `failed`；2026-11-20 配上真实 key 后 **`completed` 且 `degraded=false`**，读出 `t1_us=8e-05`、`decay_visible=true` 与英文描述 |
| `p_measure_classify.qwf` | `failed`：`Model not loaded`（image_service 未训练/未加载模型）✓ 失败语义正确 |
| `p_measure_read_analyze.qwf` | `failed`：`实验模拟器未初始化，且当前没有可用的模拟后端`——contrast 走异步 job 的 lqcs 代码片段，qiskit 后端不支持 |

## 3. qiskit 后端的三个限制（不是 bug，是能力边界）

1. **缺少 DataVault 数据集**：`experiment` 节点的结果中 `dataset_id` 为 `null`，
   因此 `read`（contrast 需要整数 datasetId）与 `analyze`（`qter.fitData` 需要 dataset_id）均缺少输入。
2. **contrast 的实现片段依赖 lqcs**：即便提供 datasetId，qiskit 后端也无法执行该读出对比度片段。
3. **图像类节点的输入可用，但服务需另行配置**：`vlm` 与 `classify` 消费的是 `plotPath` 指向的图像，
   qiskit 后端同样产出该图像，因此输入侧不构成限制；实际的前置条件是 VLM 服务与图像分类服务
   自身的配置与模型加载状态。

结论：**qiskit 演示路径下，只有 experiment / 控制类 / ask / decide / say 是可用的**；
read/analyze 属于"真机 + LabRAD"才有的能力。写工作流时要么避开，要么用 `onError continue`。

## 4. 本轮修掉的一个真问题

`server.py` 的 `image_analysis` 分支原先**把两个子请求的异常吞进 `raw_params`**，
节点照样 `completed`、流程 `outcome=achieved` —— 一条"压根没看懂图"的流程对外宣称成功。
现在：

- 两个子请求都失败 → `status=failed`，`error` 里带 `/extract_params`、`/describe` 的面包屑
- 只有一个失败 → `status=completed` + `degraded=true` + `vlm_errors` 非空
- 两个都成功 → `degraded=false`

回归测试：`server/tests/test_workflow_vlm_node.py`（3 passed）。

## 5. `config/qubitclient.json`（本机缺件，已补模板）

`decide` 节点读 `config/qubitclient.json` 的 `llm_decision` 段；`vlm` 的 qubitclient_service
读同一个文件的 `llm` 段。**两者不是一回事**：

| 段 | 消费者 | 生效时机 | key 来源 |
|----|--------|----------|----------|
| `llm_decision` | workflow_service 的 `decision` 节点 | 每次调用重读 → 立即生效 | `api_key` 或 `api_key_env`（可读 `.env`） |
| `llm` | qubitclient_service :3010（VLM） | 启动时读；改完 `POST /reload` 或重启 | 只认文件里的 `api_key`；`QubitLLM` 不读环境变量 |
| `url` / `api_key` | 其它直连 QubitClient 云端的调用方 | — | 顶层 JWT |
| `generate` | 图像生成（Qwen-Image-Edit，另一套端点） | — | 该段自带 `api_key` |

仓库里现在有 `config/qubitclient.example.json`（模板，入库）；
真文件 `config/qubitclient.json` 在 `.gitignore` 里，绝不入库。

**2026-11-20 已配好并实测跑通**（模型 `nv-community/Ising-Calibration-1-35B-A3B`，
OpenAI 兼容端点 `http://8.134.196.16:13001/v1`）。端到端 `p_measure_vlm.qwf` 走
:3008 → :3010 → VLM，`v1` 节点返回：

```json
{"status": "completed", "degraded": false, "vlm_errors": [],
 "t1_us": 8e-05, "decay_visible": true,
 "description": "The plot shows the decay of the qubit's excited state population ...",
 "metrics": {"t1_us": 8e-05, "decay_visible": true, "plot_type": "line"}}
```

配套改的两处（都在 `services/qubitclient_service/server.py`）：

1. **`vendor/QubitClient` 自动挂 `sys.path`**（`_find_vendor_qubitclient()`）——
   不要求先 `pip install -e vendor/QubitClient`；否则 `from qubitclient.llm import ...`
   报 ModuleNotFoundError，表现成"配置对了还是跑不通"。
2. **配置自愈 + 热重载**：`_ensure_loaded()` 在 `initialized=false` 时每次请求重试读盘
   （本机文件是 gitignored 的，第一次启动时不存在，原来是永久 503 只能重启）；
   `POST /reload` 强制重读并丢弃旧 client（换 key / 换 model 不用重启进程）。
   回归测试：`tests/test_qubitclient_service_reload.py`（5 passed）。

VLM 的 `imagePath` 来自上游 `measure` 出的图（qiskit 也会出图），所以 qiskit 后端下
`vlm` 节点确实"看得到图"——这是它和 `classify`/`analyze` 的关键差别。

## 6. 还没解决的（按优先级）

1. **classify**：image_service 需要先训练/加载模型（`model_loaded: false`）。
2. **read/analyze 在 qiskit 下**：要不要做"从 experiment 结果直接拟合"的本地通路，待定。
3. **`vlm c1` 的字段语义**：`parse_dataset_ref` 会把无点号的 ref 补成 `$c1.dataset`，
   而 `image_analysis` 要的是图片路径。现在能跑是因为 `measure` 之后上游把图路径写进了节点；
   语义仍然别扭，考虑在上游 lint 加一条诊断。

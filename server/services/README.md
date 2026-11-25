# server/services/ — 微服务层

平台的业务实现。Express 网关在 `server/src/index.ts`（`:3002`），网关把 `/api/...` 请求
代理到下面这些 Python 微服务；也可以直接 curl 微服务端口调试。

## 微服务一览

| 服务 | 端口 | 入口 | 职责 |
|------|------|------|------|
| `llm_service` | 3006 | `llm_service/server.py` | LLM 网关 —— MiniMax / OpenAI / DeepSeek 统一入口 |
| `quantum_service` | 3003 | `quantum_service/server.py` | 量子测控服务 —— LabRAD 通信与真实实验执行；后端策略 `lqcs → qiskit → offline_sim` |
| `analysis_service` | 3004 | `analysis_service/server.py` | 数据分析服务 —— DataLab 绘图与统计 |
| `agent_service` | 3005 | `agent_service/server.py` | Agent 服务 —— QuantumAgent，ReAct 决策 |
| `image_service` | 3007 | `image_service/server.py` | 图像服务 —— PyTorch / ONNX 推理 |
| `workflow_service` | 3008 | `workflow_service/server.py` | 工作流服务 —— 内置任务编排与 QWF 执行；含 `dsl/` 门面（转发上游 OpenQWF） |
| `task_queue` | 3009 | `task_queue/server.py` | 任务队列服务 —— 异步作业排队 |
| `qubitclient_service` | 3010 | `qubitclient_service/server.py` | 比特客户端 —— VLM 看图（`/extract_params` `/describe`）；配置见 `server/config/qubitclient.example.json` |
| `qca_service` | 3011 | `qca_service/server.py` | QCA 服务 —— 量子校准分析 |
| `hermes_service` | 3012 | `hermes_service/server.py` | Hermes Agent 服务 —— AI 对话与技能路由 |

端口与启动脚本以 `server/config/services.json` 为准（`mode.use_microservices` 总开关，
`QMCLAW_USE_SERVICES=false` 可退回单体模式）；`qubitclient` 由
`server/scripts/start_all_services.py` 单独拉起。

## 启动

```bash
cd server
python scripts/start_all_services.py --all     # 全部；也可只点名启动某个服务
```

脚本必须在 `server/` 目录内运行（服务脚本路径相对 `server/`）。启停由人控制。

## 目录

| 路径 | 内容 |
|------|------|
| `base/` | `base_service.py`（服务基类：生命周期、健康检查、请求封装）与 `service_registry.py`（注册表） |
| `common/` | 跨服务共享模块：配置 `config.py`、能力 `capabilities.py`、错误 `errors.py` `exceptions.py`、日志 `logging.py`、回放 `replay.py` `regression.py`、运行记录 `runrecord.py`、运行治理 `governance.py` `exec_policy.py`、指标 `metrics.py`、记忆 `memory_manager.py` `memory_store.py`、状态机 `state_machine.py` `state_evidence.py` `pair_state.py` `qubit_state.py`、路由 `router.py`、画像 `profile.py`、模板 `templates.py` `template_store.py`、离线数据 `offline_data_lab.py` `offline_data_provider.py` `offline_variant_generator.py`、追踪 `trace.py`、量子工具 `quantum_tools/` 等 30+ 模块 |
| `client_proxy.py` | 微服务之间的 HTTP 调用封装 |
| `<name>_service/` | 各服务自身的实现目录 |

## 约定

- 新增服务：先在 `server/config/services.json` 登记（`script` / `port` / `health_path`），
  再在 `server/src/services/serviceProxy.ts` 补代理条目，最后在 `server/services/common/service_groups.py`
  登记端口分组（`checks/_service_groups_check.py` 会校验这几处一致）。
- 服务里**不要**自己 `import services` 包顶层（会拉起全部微服务）；共享代码放 `common/` 并按需导入。
- 无真机也要能起：`labrad` 只在 `common/config.py` 的函数内 import，`qiskit` 后端惰性加载且有异常兜底。
- 改动后跑 `python checks/run_all_checks.py`（在仓库根执行）。

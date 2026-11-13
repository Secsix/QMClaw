# QMClaw 开发进度

## 2026-11-06: 自动化单比特标定优化方案整理

### 完成内容
- 审阅 `C:\Users\lqcs\Desktop\曙光测试日志\单比特自动化测试优化` 中的 PDF、XMind 和图片资料。
- 提炼出：S21 图片输入 → 局部 S21 腔频候选 → ZPA 范围识别 → ZPA 网格 spectroscopy/IQraw/PiAmp 判活 → 早停 → Ramsey/S21_dis/Pi/T1 等定向优化。
- 结合现有 `tools/qubit_health_report`、`tools/s21_photo_reader`、`backends/lqcs/experiments.py` 设计在线/离线分层脚本架构、质量门禁、状态机和写闸门。
- 新增文档：`docs/SINGLE_QUBIT_AUTO_CALIBRATION_DESIGN.md`

### 验证
- XMind `content.json` 成功提取出完整主题树。
- 已读取现有 IQraw/Rabi/Ramsey/S21 指标和单比特流程实现，确认可复用接口。
- PDF 为单页导出图；当前模型不支持图像输入，因此 PDF/图片中的细节以 XMind 文本和仓库现有可读实现交叉核对，未声称完成视觉核验。

## 2026-11-06: 自动化单比特标定方案验证

### 验证结果
- `tools/qubit_health_report/test_health_report.py`：使用 `C:\Users\lqcs\Programs\Python\Python311\python.exe`，9/9 PASS。
- `tools/s21_photo_reader/tests/test_assign.py`：UTF-8 控制台下 PASS，6 个场景通过，9/9 mock 写入成功。
- `tools/s21_photo_reader/tests/test_tool.py`：未通过，项目 `.pylibs/onnxruntime` 在当前 Python 3.11 环境加载 C API 时 DLL 缺失；当前 DSH Python 3.12 又未安装 onnxruntime。该失败属于 OCR 运行时依赖问题，未修改依赖或真机环境。
- 关键 Python 模块 `py_compile` 通过。
- 文档中的 `sq.*` 调用与 `backends/lqcs/experiments.py` 现有配置交叉检查通过；`s21_zpa/s21_zpa2d` 实际签名仍需目标测控环境验证。

## 2026-11-07: 自动化单比特标定策略代码实现

### 完成内容
- 新增 `tools/single_qubit_auto_calibration/` 独立模块。
- 实现数据模型、配置加载、状态机、安全闸门和参数范围校验。
- 实现 IQraw、Rabi/PiAmp、Spectroscopy、S21、Ramsey 指标与质量门禁。
- 实现策略决策：腔频重试、ZPA 扩展/细化、读出优化、f10/PiAmp 优化、PIDF/Ramsey、T1/ZPA 扫描、最终验收。
- 实现 MockAdapter、SqAdapter、LabRADAdapter；真实执行/写入默认关闭。
- 实现离线 Mock 工作流、候选工作点、状态转换、JSON/Markdown 报告和 `assignment_plan.py`。
- 新增 CLI `plan` 模式以及单元/集成测试。

### 验证
- 新模块 unittest：3/3 PASS。
- CLI plan 模式通过。
- 新模块全量 `py_compile` 通过。
- Mock 流程生成并检查 `run.json`、`report.md`、`assignment_plan.json`、`assignment_plan.py` 后已清理临时产物。
- 未连接 LabRAD、未启动服务、未执行真机实验、未写硬件参数。

## 2026-09-23: Hermes 扩展功能 (Memory + Skills + Cron)

### 目标
借鉴 hermes-hudui 项目的记忆管理、Skills 动态管理和 Cron 定时任务功能，集成到 QMClaw Hermes 微服务。

### 需求确认
- 存储方式：QMClaw 独立存储（不与 Hermes 共用文件）
- Skills 来源：QMClaw 独立管理
- Cron 执行：两者都支持（quantum/hermes/script）
- 现有代码：仅新增，不修改
- WebSocket 通知：广播给所有客户端（单用户系统）
- 量子比特参数：支持指定 qubit 字段

### 架构
```
qmclaw-web (前端)
    ↓ API
qmclaw-server Express (:3002)
    ↓ proxy
hermes_service (:3012)
    ├── memory.py      - 记忆管理
    ├── skills.py     - Skills 管理
    └── cron.py        - Cron 定时任务
    ↓ 存储
data/
    ├── memories/     - MEMORY.md, USER.md
    ├── skills/       - *.md 文件
    └── cron/         - jobs.json
```

### Phase 1: Cron 定时任务（已完成）

#### 后端 APScheduler 集成
- [x] `services/hermes_service/cron.py` - APScheduler 集成
  - `_init_scheduler()` - BackgroundScheduler 初始化
  - `_schedule_job()` - 将任务添加到调度器
  - `_unschedule_job()` - 从调度器移除任务
  - `_execute_job_async()` - 异步执行任务（调度器回调）
  - `_execute_quantum_job()` - 调用 quantum_service
  - `_execute_hermes_job()` - 调用 hermes_service /chat
  - `_execute_script_job()` - 执行 shell 命令
  - `_notify_job_result()` - WebSocket 广播任务结果
  - `_restore_scheduled_jobs()` - 启动时恢复任务
  - `shutdown_scheduler()` - 关闭调度器
  - `init_cron()` - 初始化入口
  - qubit 字段支持（用于量子测控任务）
  - `_on_job_executed()` - 任务执行事件回调

- [x] `services/hermes_service/approval_ws.py` - 添加 `broadcast()` 方法

- [x] `services/hermes_service/server.py` - 调度器初始化
  - `before_start()` 中调用 `cron_module.init_cron()`
  - `_handle_cron()` 支持 qubit 参数

#### 前端 WebSocket 监听
- [x] `src/components/HermesExtensionsPanel.tsx` - CronSection
  - WebSocket 连接监听 `cron_job_result` 事件
  - 任务执行结果通知横幅（5秒自动消失）
  - qubit 字段显示
  - prompt 字段输入（textarea）
  - 删除任务按钮
  - CronJobResult 接口定义

#### Gateway 路由
- [x] `src/index.ts` - 添加 DELETE /api/hermes/cron/:id

#### API 端点
- [x] `src/lib/api.ts` - 添加 `hermesDeleteCronJob`

### 待测试
- [ ] 启动后端服务测试 APScheduler 集成
- [ ] 创建 Cron 任务测试定时调度
- [ ] WebSocket 任务结果通知测试
- [ ] 量子测控任务执行测试
- [ ] Hermes Agent 任务执行测试

### Phase 2: Skills CRUD（待开发）
- [ ] 后端创建/编辑/删除 API
- [ ] 前端表单和操作按钮

### Phase 3: Memory 管理完善（待开发）
- [ ] 分类筛选
- [ ] 搜索功能

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/hermes/memory | 获取记忆状态 |
| POST | /api/hermes/memory/add | 添加记忆 |
| POST | /api/hermes/memory/edit | 编辑记忆 |
| POST | /api/hermes/memory/delete | 删除记忆 |
| GET | /api/hermes/skills | 获取 Skills 列表 |
| GET | /api/hermes/skills/:name | 获取 Skill 内容 |
| POST | /api/hermes/skills/:name/enable | 启用 Skill |
| POST | /api/hermes/skills/:name/disable | 禁用 Skill |
| GET | /api/hermes/cron | 获取 Cron 任务 |
| POST | /api/hermes/cron | 创建任务 |
| DELETE | /api/hermes/cron/:id | 删除任务 |
| POST | /api/hermes/cron/:id/pause | 暂停任务 |
| POST | /api/hermes/cron/:id/resume | 恢复任务 |
| POST | /api/hermes/cron/:id/run | 立即运行 |

---

## 2026-09-22: Hermes 聊天界面增强（Markdown 渲染 + 工具调用卡片）

### 目标
借鉴 hermes-hudui 项目设计，增强 QMClaw Hermes 聊天界面的 Markdown 渲染、代码高亮、思考过程折叠和工具调用卡片功能。

### 修改文件

#### 新建文件
- `qmclaw-web/src/lib/markdown.tsx`
  - MarkdownContent 组件 - ReactMarkdown 渲染
  - CopyButton 组件 - 代码复制按钮
  - 支持: 代码高亮、复制、引用、链接、表格、列表、标题样式
  
- `qmclaw-web/src/lib/reasoning-block.tsx`
  - ReasoningBlock 组件 - 可折叠思考过程卡片
  - 左侧黄色边框 + 🧠 图标
  - 默认收起，点击展开

- `qmclaw-web/src/lib/tool-call-card.tsx`
  - ToolCallCard 组件 - 可折叠工具调用卡片
  - 状态指示: 运行中(黄) / 完成(绿) / 错误(红)
  - ToolCallsList 组件 - 工具调用列表包装器

#### 修改文件
- `qmclaw-web/package.json`
  - 添加依赖: react-markdown, remark-gfm, rehype-highlight, highlight.js

- `qmclaw-web/src/components/HermesChatPanel.tsx`
  - 导入新组件: MarkdownContent, ReasoningBlock, ToolCallsList
  - 重构消息渲染逻辑，使用新组件替代原有简陋实现

### 依赖安装
```bash
npm install react-markdown remark-gfm rehype-highlight highlight.js
```

### 状态
- [x] Markdown 渲染组件创建
- [x] 思考过程折叠组件创建
- [x] 工具调用卡片组件创建
- [x] HermesChatPanel 重构
- [x] npm install 完成（使用 npmmirror 镜像）
- [x] 构建验证通过（仅 page.tsx 有预先存在的类型错误）

### 验证
代码已编译成功。page.tsx 的类型错误是预先存在的，与本次修改无关。

### 待测试
启动前端服务后测试：
- [ ] 消息 Markdown 渲染（代码高亮、表格、引用）
- [ ] 代码块复制按钮
- [ ] 思考过程折叠/展开
- [ ] 工具调用卡片状态和展开

---

## 2026-09-22: Hermes 消息图片显示

### 目标
在 Hermes 聊天界面中显示工具调用返回的图片，并支持下载和全屏查看。

### 修改文件

#### 前端
- `src/components/HermesChatPanel.tsx`
  - 新增 `images` 字段到 `HermesMessage` 接口
  - 在 `tool_complete` 事件处理中添加图片提取逻辑
  - 新增 `downloadImage()` 函数
  - 新增图片渲染组件（显示、下载、全屏按钮）

#### 后端
- `services/common/quantum_tools/analysis_tools.py`
  - 新增 `GetLatestDatasetTool` - 获取最新数据集工具
  - 新增 `analysis_get_latest_dataset` 工具

#### 核心修复
- `services/hermes_service/server.py`
  - 修复 `tool_complete_callback` 参数（4个参数而非2个）
  - 现在可以正确获取工具的 result 数据

### 图片提取格式
支持从工具结果中提取多种图片格式：
- `image` - base64 data URL (Analysis Service 返回格式)
- `plotPath` - HTTP URL 或 base64
- `plotUrl` - HTTP URL
- `data.image` - ToolResult 嵌套格式

### 新增工具
- `qmclaw_analysis_get_latest_dataset` - 获取最新的数据集信息（ID, name, qubit, experiment_type）

### 状态
- [x] 实现完成
- [ ] 重启 Hermes Service 测试

---

## 2026-09-22: Hermes MCP 工具集成（Analysis + Workflow）

### 目标
让 Hermes 智能体能调用测控系统的 Analysis 和 Workflow API，实现自然语言驱动的实验数据分析和自动化工作流控制。

### 修改文件

#### 新建文件
- `services/common/quantum_tools/analysis_tools.py` - Analysis 工具（5个工具）
  - `analysis_plot_offline` - 绘制离线数据
  - `analysis_get_stats` - 获取统计信息
  - `analysis_generate_variant` - 生成数据变体
  - `analysis_execute` - 执行分析代码
  - `analysis_list_variants` - 列出数据变体

- `services/common/quantum_tools/workflow_tools.py` - Workflow 工具（6个工具）
  - `workflow_list` - 列出工作流
  - `workflow_create` - 创建工作流
  - `workflow_run` - 运行工作流
  - `workflow_status` - 获取状态
  - `workflow_cancel` - 取消工作流
  - `workflow_stats` - 获取统计

- `services/hermes_service/mcp_toolsets.py` - 统一的 MCP 工具集管理
  - `ToolsetBridge` - 工具集桥接器
  - `MCPBridgeManager` - MCP 桥接管理器

#### 修改文件
- `services/hermes_service/server.py`
  - 新增 MCP 桥接器初始化
  - 新增工具注册逻辑（在 AIAgent 创建前）
  - 更新 `_handle_quantum_tools` 支持多命名空间

### 工具命名规范
- Quantum: `qmclaw_{name}` (如 `qmclaw_get_qubits`)
- Analysis: `qmclaw_analysis_{name}` (如 `qmclaw_analysis_plot_offline`)
- Workflow: `qmclaw_workflow_{name}` (如 `qmclaw_workflow_run`)

### 状态
- [x] 分析探索
- [x] 实现 Analysis 工具
- [x] 实现 Workflow 工具
- [x] 创建 MCP 桥接管理器
- [x] 集成到 server.py
- [x] 修复工具注册和解析问题
- [ ] E2E 测试验证

---

## 2026-09-22: Hermes Web 会话历史管理

### 目标
为 Hermes Web UI 添加会话列表侧边栏，支持会话切换、创建和删除功能。

### 需求确认
- 会话列表 UI：侧边栏（左侧），默认展开
- 会话切换：单会话模式（不支持多会话并发）
- 会话列表：仅显示列表，无需搜索/过滤
- 在线状态：需要显示 WebSocket 连接状态

### 修改文件

#### 后端
- `services/hermes_service/server.py`
  - 新增 DELETE /sessions/:id 端点
  - 新增 `_handle_delete_session()` 方法

#### 前端
- `src/lib/api.ts`
  - 新增 `hermesGetSessions()` - 获取会话列表
  - 新增 `hermesGetSessionMessages(sessionId)` - 获取会话消息
  - 新增 `hermesDeleteSession(sessionId)` - 删除会话

- `src/components/HermesChatPanel.tsx`
  - 新增 `Session` 接口
  - 新增会话状态：`sessions`, `showSessionList`, `loadingSessions`, `deletingSessionId`
  - `sessionId` 改为可变状态（支持切换）
  - 新增函数：`loadSessions()`, `switchSession()`, `createNewSession()`, `deleteSession()`
  - 新增会话列表侧边栏 UI
  - 新增在线状态指示器（绿色=在线，红色=离线）
  - 新增头部会话切换按钮

### UI 布局
```
┌────────────────────────────────────────────────────────────┐
│ [☰] Model:[▼] [Web] [Vision] [Terminal]     ● Online     │  ← 头部
├──────────────┬─────────────────────────────────────────────┤
│   Sessions   │                                             │
│  ──────────  │           Chat Area                        │
│  ○ S1 (3)   │                                             │
│  ● S2 (7)   │                                             │
│  ○ S3 (12)  │                                             │
│  ──────────  │                                             │
│  [+ New]    │                                             │
├──────────────┴─────────────────────────────────────────────┤
│ [输入框...]                                      [Send]    │
└────────────────────────────────────────────────────────────┘
```

### 待完成
- [ ] 测试会话切换功能
- [ ] 测试会话删除功能
- [ ] 测试历史消息加载

---

## 2026-09-20: 统一量子测控工具集和 API

### 背景
当前 QMClaw 系统有三个服务可以调用测控实验命令：
- **agent_service** (3005): 直接调用 LabRAD，执行 `sq.*` 函数
- **qca_service** (3011): 结构化实验接口，基于 LangChain
- **hermes_service** (3012): 通用 Agent，需要扩展工具

**问题**：
1. 工具定义风格不同：agent 用自定义 `Tool` 类，qca 用 LangChain `@tool`
2. 参数格式不统一：agent 用代码字符串，qca 用结构化参数
3. 返回格式不一致：各服务返回格式各异

**目标**：统一工具集、统一 API、统一返回格式，支持日常测控实验场景。

### 架构
```
┌─────────────────────────────────────────────────────────────┐
│                    Unified Quantum Tools                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              ToolRegistry (Singleton)               │    │
│  │  ┌─────────────┐ ┌─────────────┐ ┌───────────┐ │    │
│  │  │RunCodeTool  │ │GetQubits   │ │Data Tools │ │    │
│  │  └─────────────┘ └─────────────┘ └───────────┘ │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
          ▲                ▲                ▲
          │                │                │
    ┌─────┴─────┐  ┌─────┴─────┐  ┌────┴────┐
    │agent_svc   │  │ qca_svc   │  │hermes_svc│
    │  adapter   │  │  adapter  │  │ MCP Tool │
    └───────────┘  └───────────┘  └──────────┘
```

### 已完成

#### Phase 1: 核心框架
- [x] `services/common/quantum_tools/__init__.py` - 统一导出
- [x] `services/common/quantum_tools/base.py` - 工具基类和统一返回格式
  - `ToolResult` - 统一返回格式
  - `ToolSchema` - 工具 schema
  - `ParameterSchema` - 参数 schema
  - `QuantumTool` - 抽象基类
  - `ExperimentResult` - 实验结果数据结构
  - `QubitInfo` - 量子比特信息
- [x] `services/common/quantum_tools/errors.py` - 错误码定义
  - `ErrorCode` 枚举
  - `QMClawError` 异常基类
  - `ValidationError`, `NotFoundError`, `TimeoutError`, `LabRADError` 等
- [x] `services/common/quantum_tools/registry.py` - 工具注册中心
  - `ToolRegistry` 类（每次实例化都是新实例，非单例）
  - `register_tool()` 注册工具
  - `execute()` 执行工具
  - `get_definitions()` 返回 OpenAI 格式定义
  - `get_stats()` 执行统计
- [x] `services/common/quantum_tools/types.py` - 数据类型定义

#### Phase 2: 核心工具实现
- [x] `services/common/quantum_tools/execution.py` - 实验执行工具
  - `RunCodeTool` - 直接执行代码
  - `RunExperimentTool` - 执行命名实验
  - `ListExperimentsTool` - 列出实验
  - `GetExperimentSchemaTool` - 获取实验 schema
- [x] `services/common/quantum_tools/qubit.py` - 量子比特工具
  - `GetQubitsTool` - 获取量子比特列表
  - `GetQubitParamsTool` - 获取量子比特参数
  - `SetQubitParamsTool` - 设置量子比特参数
- [x] `services/common/quantum_tools/data.py` - 数据查询工具
  - `ListHistoryTool` - 查询实验历史
  - `GetExperimentDataTool` - 获取实验数据
  - `GetArrayDataTool` - 获取数组数据
  - `GetStatsTool` - 获取统计数据

#### Phase 3: 服务适配（每个服务独立注册）
- [x] `services/agent_service/adapter.py` - Agent Service 适配器
  - `setup_agent_tools()` - 注册工具（直接调用 LabRAD）
  - `AgentServiceAdapter` 类
- [x] `services/agent_service/server.py` - 集成统一工具 + `/api/v1/` 路由
- [x] `services/qca_service/adapter.py` - QCA Service 适配器
  - `setup_qca_tools()` - 注册工具（HTTP 调用 quantum_service）
  - `QCAServiceAdapter` 类
- [x] `services/hermes_service/quantum_toolset.py` - Hermes MCP 工具服务器
  - `setup_hermes_tools()` - 注册工具（HTTP 调用 quantum_service）
  - `QuantumMCPServer` 类
- [x] `services/hermes_service/server.py` - 集成量子工具集 + `/quantum-tools` 端点

### 架构说明

```
┌─────────────────────────────────────────────────────────────────────┐
│                   智能体服务（通过 HTTP 调用）                      │
│                                                                  │
│  agent_service  ──────────────┐                                  │
│  qca_service    ────────────┼──> quantum_service (:3003)        │
│  hermes_service ──────────────┘                                  │
│                                                                  │
│  负责：自然语言理解、任务规划、工具调用                            │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 直接调用
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   quantum_service                                 │
│  负责：LabRAD 连接、实验执行、量子比特管理                         │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   LabRAD / 硬件设备                               │
└─────────────────────────────────────────────────────────────────────┘
```

每个服务的 ToolRegistry 是独立实例，工具定义相同且都通过 HTTP 调用 quantum_service。

### 统一 API 端点

#### agent_service (3005)
```
GET  /api/v1/tools           - 列出所有工具
GET  /api/v1/tools/{name}   - 获取工具 schema
POST /api/v1/tools/{name}   - 执行工具
GET  /api/v1/qubits          - 获取量子比特
GET  /api/v1/experiments     - 列出实验
GET  /api/v1/health          - 健康检查
```

#### hermes_service (3012)
```
GET  /quantum-tools          - 列出量子工具
POST /quantum-tools/execute   - 执行量子工具
```

### 统一返回格式
```python
{
    "success": true,
    "data": {...},
    "error": {
        "code": "NOT_FOUND",
        "message": "Resource not found",
        "hint": "Call GET /qubits to see available"
    },
    "request_id": "req_abc123",
    "timestamp": "2026-09-20T14:30:45Z"
}
```

### 待验证
- [ ] 模块导入测试 (已通过)
- [ ] 服务启动测试
- [ ] 工具注册测试
- [ ] 跨服务 API 一致性测试
- [ ] hermes_service 量子工具集成测试

---

## 2026-09-17: Quantum/Analysis 服务降级模式

### 背景
当 LabRAD 或 lqcs 服务不可用时（如 lqcs 中的 Cython/Twisted 兼容性问题），quantum_service 和 analysis_service 应该能够优雅降级，显示本地缓存数据而不是直接崩溃。

### 目标
- 服务启动失败时不崩溃，进入降级模式
- 支持重连策略（指数退避）
- 最大重试次数后停止，显示降级状态
- 降级模式下返回本地缓存数据
- 前端显示降级状态和提示信息

### 架构
```
                    ┌─────────────────┐
                    │  Service Start  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Try Connect     │
                    │  LabRAD/lqcs     │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
       ┌──────▼──────┐              ┌───────▼──────┐
       │  Connected  │              │   Failed     │
       └──────┬──────┘              └───────┬──────┘
              │                             │
       ┌──────▼──────┐              ┌───────▼──────┐
       │  Healthy    │              │ Degraded Mode │
       │  Full Func  │              │ + Retry Timer│
       └─────────────┘              └──────┬───────┘
                                            │
                                    (max_attempts 次后停止)
                                            ▼
                                    ┌───────────────┐
                                    │  Permanent    │
                                    │  Degraded     │
                                    └───────────────┘
```

### 配置文件
```json
// config/fallback_config.json
{
  "quantum_service": {
    "enabled": true,
    "fallback_data_path": "data/fallback/quantum",
    "retry": {
      "max_attempts": 5,
      "base_delay": 2,
      "max_delay": 30
    }
  },
  "analysis_service": {
    "enabled": true,
    "fallback_data_path": "data/fallback/analysis",
    "retry": {...}
  }
}
```

### 已完成

#### 降级状态管理器
- [x] `services/common/fallback_manager.py` - FallbackManager 类
  - `ConnectionState` 枚举: DISCONNECTED, CONNECTING, CONNECTED, DEGRADED, DEGRADED_PERMANENT
  - 重连策略: 指数退避 (`base_delay * 2^attempt`)
  - 状态变更回调机制
  - 本地降级数据加载 (`get_fallback_data()`)
  - 定时器管理 (`start_reconnect_timer()`)
- [x] `services/common/__init__.py` - 导出 FallbackManager, ConnectionState

#### Quantum Service 降级
- [x] `services/quantum_service/server.py` 修改
  - 添加 `_fallback: FallbackManager` 成员
  - 修改 `before_start()`: 连接失败时进入降级模式，启动重连定时器
  - 添加 `_do_reconnect()`: 重连回调函数
  - 添加 `_load_fallback_data()`: 加载降级数据 (qubits.json, experiments.json)
  - 修改 `_ensure_connected()`: 处理降级状态
  - 修改 `_handle_health()`: 返回 `fallback_mode`, `fallback_state`, `fallback_message`
  - 修改 `_handle_connect()`: 支持手动重连
  - 修改 `_handle_qubits()`: 降级模式下返回本地数据
  - 修改 `_handle_experiments()`: 降级模式下返回本地数据
  - 修改 `_handle_execute()`: 降级模式下拒绝执行
  - 修改其他 handler: 添加降级状态检查
  - 添加 `_get_fallback_message()`: 生成用户友好的降级提示

#### Analysis Service 降级
- [x] `services/analysis_service/server.py` 修改
  - 添加 `_fallback: FallbackManager` 成员
  - 修改 `before_start()`: 连接失败时进入降级模式
  - 添加 `_do_reconnect()`: 重连回调函数
  - 添加 `_load_fallback_data()`: 加载降级数据 (datasets.json)
  - 修改 `_handle_health()`: 返回降级信息
  - 修改 `_handle_connect()`: 支持重连
  - 修改 `_handle_execute()`: 降级模式下拒绝执行
  - 修改 `_handle_datasets()`: 降级模式下返回本地数据
  - 添加 `_get_fallback_message()`: 生成降级提示

#### Gateway API 更新
- [x] `src/index.ts` 修改
  - 修改 `/hardware/status`: 返回 `fallback` 对象 (mode, state, message)
  - 修改 `/hardware/quick`: 返回 `fallback` 对象，更新 `labrad` 状态为 "degraded"
  - 添加日志过滤器标签: `[FallbackManager]`, `[quantum_service]`, `[analysis_service]`

#### 降级数据示例
- [x] `data/fallback/quantum/qubits.json` - 量子比特示例数据
- [x] `data/fallback/quantum/experiments.json` - 实验函数示例数据
- [x] `data/fallback/analysis/datasets.json` - 数据集示例数据

#### 配置文件
- [x] `config/fallback_config.json` - 降级模式配置

### 前端 API 响应格式
```typescript
// GET /hardware/quick
{
  labrad: "degraded",
  llm: "ready",
  ray: "via_quantum",
  datavault: "disconnected",
  fallback: {
    mode: true,
    state: "degraded",
    message: "LabRAD 连接中断，正在尝试重连（3/5）..."
  },
  message: "降级模式 - LabRAD 连接中断，正在尝试重连（3/5）..."
}

// GET /hardware/status
{
  overall: "degraded",
  timestamp: "...",
  services: {...},
  devices: {},
  issues: ["LabRAD not connected"],
  fallback: {
    mode: true,
    state: "degraded_permanent",
    message: "LabRAD 不可用（已停止重连）。最后错误：连接超时"
  }
}
```

### 待验证
- [ ] quantum_service 启动测试（正常模式和降级模式）
- [ ] analysis_service 启动测试
- [ ] 降级数据加载测试
- [ ] 重连机制测试（模拟 LabRAD 不可用）
- [ ] 前端降级状态显示测试
- [ ] 永久降级状态测试（max_attempts 次重连失败后）

---

## 2026-09-16: Workflow 历史记录微服务迁移

### 背景
系统已从 Legacy 模式迁移到微服务模式，但 workflow 历史记录功能仍在 Express 层。微服务重启会导致运行记录丢失。

### 目标
在 `workflow_service/server.py` 中实现历史记录功能，与现有的 TypeScript 版本功能对标。

### 架构
```
Browser → Express (:3002) → workflow_service (:3008)
                            → data/workflow-runs/*.json
```

### 已完成

#### workflow_service/server.py 增强
- [x] 添加数据目录配置 `DATA_DIR = ROOT_DIR / "data" / "workflow-runs"`
- [x] 添加 `_ensure_data_dir()` 确保目录存在
- [x] 添加历史记录数据类: `WorkflowRunNodeInput`, `WorkflowRunNodeOutput`, `WorkflowRunNode`, `WorkflowRun`
- [x] 添加数据锁 `_runs_lock = threading.Lock()`
- [x] 添加 CRUD 方法:
  - `_list_runs()` - 列出运行记录（支持 workflowId/workflowName 筛选）
  - `_get_run()` - 获取单条运行记录
  - `_save_run()` - 保存运行记录
  - `_delete_run()` - 删除运行记录
  - `_delete_runs_by_workflow()` - 删除某工作流的所有运行记录
  - `_get_stats()` - 获取工作流统计信息
- [x] 添加 `_persist_workflow_run()` - 将工作流运行结果持久化到磁盘
- [x] 修改 `_run_workflow_async()` 在工作流完成时调用持久化方法
- [x] 添加历史记录 API 路由处理函数:
  - `_handle_list_runs()` - GET /runs
  - `_handle_get_run()` - GET /runs/<id>
  - `_handle_delete_run()` - DELETE /runs/delete
  - `_handle_delete_runs_by_workflow()` - DELETE /runs/workflow/<workflowId>
  - `_handle_get_stats()` - GET /runs/stats/<workflowId>
- [x] 修改 `handle_request()` 添加新路由

#### BaseService 增强
- [x] 添加 `do_DELETE()` 方法支持 DELETE 请求

#### Express 网关增强 (serviceProxy.ts)
- [x] 修改 `proxyToService()` 支持 DELETE 方法
- [x] 添加工作流历史记录代理路由:
  - `GET /api/workflow-runs` → `/runs`
  - `GET /api/workflow-runs/stats/:workflowId` → `/runs/stats/:workflowId`
  - `GET /api/workflow-runs/:runId` → `/runs/:runId`
  - `DELETE /api/workflow-runs/:runId` → `/runs/delete`
  - `DELETE /api/workflow-runs/workflow/:workflowId` → `/runs/workflow/:workflowId`

#### Express index.ts 清理
- [x] 注释掉原有的 Express workflow-runs 路由（原直接使用 workflowRunService.ts）

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/runs` | 列出所有运行记录（支持 workflowId/workflowName 筛选） |
| GET | `/runs/<id>` | 获取单条运行记录 |
| DELETE | `/runs/delete` | 删除运行记录 (body: {runId}) |
| DELETE | `/runs/workflow/<workflowId>` | 删除某工作流的所有运行记录 |
| GET | `/runs/stats/<workflowId>` | 获取统计信息 |

### 待验证
- [ ] workflow_service 启动测试
- [ ] 运行 workflow 后检查 `data/workflow-runs/` 目录
- [ ] API 端点测试 (curl 或前端)
- [ ] 前端 Runs/History 页面功能测试

---

## 2026-09-14: Experiments 页面布局重构

### 背景
用户希望统一展示 COMMAND、PLOT COMMAND、ANALYZE COMMAND 三个命令，并整合结果展示区域。

### 设计方案
```
┌─────────────────────────────────────────────────────────────┐
│  ▶ COMMAND    sq.iqraw(q3ld4, do_plot=True)          [▼]  │
├─────────────────────────────────────────────────────────────┤
│  ▶ PLOT CMD   qter.fitData({exp_num})                [▼]  │
├─────────────────────────────────────────────────────────────┤
│  ▶ ANALYZE    qter.fitData({exp_num}, collect=True)  [▼] │
├─────────────────────────────────────────────────────────────┤
│  📋 RESULTS                                            │
│  ├── 📊 绘图结果 (Base64 图像 + 下载按钮)              │
│  └── 📈 分析结果 (metrics 网格 + stdout)              │
├─────────────────────────────────────────────────────────────┤
│  [⚡ Execute All] [▶ Run] [其他实验快捷按钮...]         │
└─────────────────────────────────────────────────────────────┘
```

### 颜色方案
- COMMAND: `#22c55e` (绿色) - 执行
- PLOT: `#3b82f6` (蓝色) - 绘图
- ANALYZE: `#f59e0b` (橙色) - 分析
- Execute All: `#8b5cf6` (紫色)

### 已完成
- [x] `CollapsibleCommand.tsx` - 可折叠命令组件
  - 折叠时显示一行命令文本
  - 展开时显示 textarea + Run/Save 按钮
  - 左侧边框颜色区分类型
  - Ctrl+Enter 运行快捷键
- [x] `UnifiedResults.tsx` - 统一结果展示组件
  - 整合绘图结果和分析结果
  - Base64 图像显示 + 下载按钮
  - Metrics 网格展示
  - stdout 可折叠显示
- [x] `page.tsx` - 重构 Experiments Tab
  - 移除 EditableCommand、PlotCommandEditor、AnalysisCommandEditor
  - 引入 CollapsibleCommand 和 UnifiedResults
  - 添加 "Execute All" 一键执行按钮
  - 状态管理更新 (isRunningCommand/Plot/Analyze, isSaving*)
- [x] `experiment_configs.json` - 补充所有实验的 defaultCommand

### 待测试
- [ ] 折叠/展开交互
- [ ] 命令编辑和运行
- [ ] Execute All 流程
- [ ] Base64 图像显示
- [ ] 分析结果展示

---

## 2026-09-12: 服务化架构重构 Phase 1 - 基础框架

### 背景
将 monolithic `job_runner.py` (4969行) 拆分为多个独立微服务，解决代码难以维护、功能耦合、Ray初始化冲突等问题。

### 目标架构
```
Express (:3002) → Python 微服务
├── LLM Service (:3006) - 多 Provider 统一接口
├── Quantum Service (:3003) - LabRAD 连接、实验执行
├── Analysis Service (:3004) - 数据分析、绘图
├── Agent Service (:3005) - QuantumAgent、ReAct 推理
├── Image Service (:3007) - PyTorch/ONNX 推理
└── Workflow Service (:3008) - 节点调度、依赖解析
```

### 已完成

#### 服务基础设施
- [x] `services/base/base_service.py` - 服务基类
  - `_safe_print()` - UTF-8 编码安全打印
  - `BaseService` - 抽象服务类
  - `ServiceConfig` - 配置数据类
  - `ServiceStatus` - 状态枚举
  - `run_service()` - 便捷运行函数
- [x] `services/base/service_registry.py` - 服务注册机制
- [x] `services/base/__init__.py` - 导出
- [x] `services/common/` - 公共模块
  - `config.py` - 配置管理
  - `logging.py` - 统一日志
  - `exceptions.py` - 异常定义

#### LLM 服务
- [x] `services/llm_service/server.py` - LLM 推理服务
  - `/chat` - 聊天完成（MiniMax/OpenAI/DeepSeek）
  - `/models` - 模型列表
  - `/stats` - 统计信息
  - `/health` - 健康检查
- [x] `services/llm_service/__init__.py`
- [x] 已测试可用

#### 测控执行服务
- [x] `services/quantum_service/labrad_client.py` - LabRAD 客户端
  - `LabRADClient` 类
  - LabRAD 连接管理
  - Qubit 生成和管理
  - Session 切换
  - 实验模块 (`sq.*`) 访问
- [x] `services/quantum_service/server.py` - 测控执行服务
  - `/health` - 健康检查
  - `/connect` - 触发连接
  - `/status` - 连接状态
  - `/qubits` - 量子比特列表
  - `/experiments` - 实验函数列表
  - `/execute` - 执行实验代码
  - `/switch_session` - 切换会话
- [x] `services/quantum_service/__init__.py`
- [x] `services/__init__.py` - 更新导出

#### 配置和脚本
- [x] `config/services.json` - 服务配置
- [x] `scripts/start_all_services.py` - 批量启动脚本

#### 数据分析服务
- [x] `services/analysis_service/server.py` - 数据分析服务
  - `/health` - 健康检查
  - `/plot` - 绘制最新数据集
  - `/plot/historical` - 绘制历史数据集
  - `/stats` - 数据统计
  - `/analyze` - 数据分析 (basic/peak/fit)
  - `/datasets` - 数据集列表
  - `/load` - 加载数据集
- [x] `services/analysis_service/__init__.py`
- [x] `services/__init__.py` - 更新导出

#### Agent 服务
- [x] `services/agent_service/server.py` - Agent 服务
  - `/health` - 健康检查
  - `/chat` - ReAct 推理对话
  - `/chat/stream` - 流式对话
  - `/tasks` - 任务列表
  - `/tasks/status` - 任务状态
  - `/tools` - 工具列表
  - 内置工具: run_experiment, get_qubits, list_experiments
- [x] `services/agent_service/__init__.py`

#### Image 服务
- [x] `services/image_service/server.py` - 图像服务
  - `/health` - 健康检查
  - `/classify/single` - 单张图像分类
  - `/classify/folder` - 批量图像分类
  - `/train` - 模型训练
  - `/model/info` - 模型信息
  - `/training/status` - 训练状态
  - 支持 PyTorch/ONNX 后端
- [x] `services/image_service/__init__.py`

#### Workflow 服务
- [x] `services/workflow_service/server.py` - 工作流服务
  - `/health` - 健康检查
  - `/workflows` - 工作流列表
  - `/workflows/create` - 创建工作流
  - `/workflows/run` - 运行工作流
  - `/workflows/status` - 工作流状态
  - `/workflows/cancel` - 取消工作流
  - 节点类型: experiment, analysis, llm
  - 拓扑排序依赖解析
- [x] `services/workflow_service/__init__.py`

#### 任务队列服务
- [x] `services/task_queue/server.py` - 任务队列服务
  - `/health` - 健康检查
  - `/submit` - 提交任务
  - `/status` - 任务状态
  - `/list` - 任务列表
  - `/cancel` - 取消任务
  - `/retry` - 重试任务
  - `/stats` - 统计信息
  - `/poll` - 轮询获取任务（工作者）
  - `/complete` - 标记任务完成
  - 任务类型: experiment, analysis, agent_chat, workflow, image_classify, general
  - 优先级队列 (1-10)
  - 重试机制 (可配置 max_retries)
  - Redis 后端支持（可选）
- [x] `services/task_queue/__init__.py`
- [x] `config/services.json` - 添加 task_queue 服务配置
- [x] `scripts/start_all_services.py` - 更新

#### 服务注册表更新
- [x] `services/__init__.py` - 更新导出所有服务

### Phase 1 完成 - 服务增强
- [x] 增强 Quantum Service 新增端点：
  - `GET /sessions` - 获取会话列表
  - `GET /session_tree?max_depth=5` - 获取目录树
  - `GET /qubit/params?name=xxx` - 获取量子比特参数
  - `POST /qubit/set_params` - 设置量子比特参数
  - `GET /datasets?path=xxx` - 获取数据集列表
- [x] 增强 LabRAD Client 添加属性：
  - `name`, `host`, `port`, `dv` 属性
- [x] 更新 Express 网关 - 添加新路由代理
- [x] 更新 TypeScript 客户端 - 添加新方法
- [x] 修复 TypeScript 错误 (PromiseRejectedResult.reason)

### Phase 2 完成 - 客户端代理
- [x] 创建 `services/client_proxy.py` - Python 客户端代理模块
  - QuantumProxy, AnalysisProxy, LLMProxy, AgentProxy
  - WorkflowProxy, TaskQueueProxy
  - check_service_health(), get_all_health()

### Phase 3 完成 - 前端API同步
- [x] 更新 Express 网关挂载路径: `app.use('/api', createServiceProxy())`
- [x] 更新前端 `api.ts` 使用新的微服务路径结构
- [x] 路径结构 (微服务架构一目了然):

```
/api/
├── /llm/*           → LLM Service (:3006)
├── /quantum/*       → Quantum Service (:3003)
├── /analysis/*      → Analysis Service (:3004)
├── /agent/*        → Agent Service (:3005)
├── /image/*        → Image Service (:3007)
├── /workflow/*      → Workflow Service (:3008)
├── /tasks/*        → Task Queue Service (:3009)
└── /hermes/*       → Hermes Agent (job_runner.py)
```

### 自动启动微服务
- [x] 在 job_runner.py 中添加 `ServiceManager` 类
- [x] 当 `QMCLAW_USE_SERVICES=true` 时自动启动所有微服务
- [x] 使用 `atexit` 注册退出时停止所有服务
- [x] 使用 `signal` 处理 SIGINT/SIGTERM 正确停止服务

### Express 网关路径设计原则
- 简单: 一个 Router 处理所有代理路由
- 可靠: 统一超时、错误处理、响应格式
- 易维护: 路由按服务分组，清晰明了
- 可扩展: 新增服务只需添加配置和路由

### Express 网关重构 (已完成)
- [x] 重构 `src/services/serviceProxy.ts`
  - 类型定义 (ServiceConfig)
  - 配置层 (SERVICES, 超时配置)
  - 核心代理 (`proxyToService`)
  - 路由按服务分组
  - 健康检查 (`/services/health`)
  - 导出类型和工具函数

### 使用方法
```bash
# 默认模式 (不启用服务)
python scripts/job_runner.py

# 启用服务模式
export QMCLAW_USE_SERVICES=true
python scripts/job_runner.py
```

### 已完成 - 服务测试
- [x] 所有服务可独立启动
- [x] LLM 服务健康检查 ✅
- [x] LLM 服务模型列表 ✅
- [x] TaskQueue 服务 ✅
- [x] Analysis 服务 ✅
- [x] Workflow 服务 ✅
- [x] Quantum 服务 ✅
- [x] 创建 `__main__.py` 支持 `python -m services.<name>`

### 已完成 - Express 网关集成
- [x] `src/services/serviceProxy.ts` - 统一服务代理
  - `/api/llm/*` - LLM 服务代理
  - `/api/quantum/*` - 测控服务代理
  - `/api/analysis/*` - 分析服务代理
  - `/api/agent/*` - Agent 服务代理
  - `/api/image/*` - 图像服务代理
  - `/api/workflow/*` - 工作流服务代理
  - `/api/tasks/*` - 任务队列代理
  - `/api/services/health` - 所有服务健康检查汇总
- [x] `src/services/apiClients.ts` - TypeScript 服务客户端
  - llmClient, quantumClient, analysisClient
  - agentClient, imageClient, workflowClient
  - taskQueueClient, healthClient
- [x] `src/index.ts` - 挂载服务代理中间件

---

## 2026-09-07: Hermes-Agent 集成

### 目标
- 将 Hermes-Agent 作为独立 Tab 页面集成到 QMClaw Web UI
- 先独立运行，后续考虑与现有 Agent 页面融合

### 架构
```
Browser → Express (:3002) → Python subprocess → Hermes AIAgent
```

### 已完成
- [x] `scripts/hermes_runner.py` - Hermes Runner 模块
  - `HermesRunner` 类 - AIAgent 封装
  - `QMClawMemoryProvider` - 会话隔离内存提供者
  - `QMClawToolWrapper` - QMClaw 量子控制工具包装器
  - `_run_hermes_chat()` - 非流式处理
  - `_run_hermes_chat_stream()` - SSE 流式处理
  - **自动模型检测** - 根据模型名自动选择 provider/base_url
    - MiniMax: `https://api.minimax.chat/v1`
    - Anthropic: `https://api.anthropic.com/v1`
    - OpenAI: `https://api.openai.com/v1`
    - DeepSeek: `https://api.deepseek.com/v1`
    - 默认: OpenRouter
- [x] `src/index.ts` - Express 路由
  - `POST /api/hermes/chat` - Hermes 聊天端点
  - `POST /api/hermes/chat/stream` - SSE 流式端点
  - `GET /api/hermes/models` - 可用模型列表（含 MiniMax M2.7）
- [x] `scripts/job_runner.py` - 后端动作处理
  - `hermes_chat` action handler
  - `hermes_chat_stream` action handler
- [x] `src/lib/api.ts` - 前端 API
  - `hermesChat()` - 非流式调用
  - `hermesChatStream()` - async generator SSE 流式处理
  - `hermesGetModels()` - 获取模型列表
- [x] `src/components/HermesChatPanel.tsx` - 前端 UI
  - 模型选择器（默认 MiniMax M2.7）
  - Toolset 切换 (web/vision/terminal/computer_use)
  - 流式响应显示
  - Thinking 块显示
  - 工具调用显示
- [x] `src/app/page.tsx` - Tab 集成
  - 添加 "hermes" Tab
  - 导入 HermesChatPanel 组件
  - 添加 Tab 按钮和内容渲染

### 模型配置
| 模型 | Provider | Base URL | 默认 |
|------|----------|----------|------|
| MiniMax-M2.7 | minimax | https://api.minimax.chat/v1 | ✅ |
| Claude Sonnet 4.6 | anthropic | https://api.anthropic.com/v1 | |
| Claude Opus 4.8 | anthropic | https://api.anthropic.com/v1 | |
| GPT-4o | openai | https://api.openai.com/v1 | |
| DeepSeek Chat | deepseek | https://api.deepseek.com/v1 | |

### 安全配置
- 默认禁用 terminal 和 computer_use toolsets
- 只启用 web 和 vision
- 会话隔离 (每会话独立 MemoryProvider)

### 模型配置集成
- [x] Hermes 使用 `model_configs.json` 系统配置
- [x] 自动从 `qmclaw-server/config/model_configs.json` 加载
- [x] 移除硬编码模型配置，handler 直接传递 model 参数
- [x] 支持 provider 自动检测和 API key 环境变量映射
- [x] 未指定模型时自动选择第一个 enabled 模型

### 待测试
- [ ] 后端服务启动测试
- [ ] Hermes Tab 页面功能测试
- [ ] 与现有 Agent 页面融合测试

---

## 2026-09-07: Agent 对话非阻塞修复

### 问题
- Agent 对话任务阻塞主进程，导致其他 API 请求超时
- SSE 流式传输端点 Promise 不resolve

### 解决方案
- 简化前端：`agent_chat` 使用非阻塞模式
- 任务通过 Python 后台线程 `_backend_queue` 执行，不阻塞 Express

### 已完成
- [x] `AgentChatPanel.tsx` - 移除复杂 SSE 处理，改用 `api.agentChat()`
- [x] `agent_chat` 通过 Python 后台队列执行，不阻塞主进程
- [x] README.md 添加 TODO 章节，记录 SSE 流式处理待办

### 待办（已记录到 README.md TODO）
- [ ] SSE 流式处理支持 - Python 后台线程 stdout 通信问题待解决

---

## 2026-09-04: 前端页面重构 - 统一左侧边栏

### 架构调整
- Tab 从 8 个精简为 4 个：experiments, workflow, agent, images
- jobs, services, agent-tools, memory 不再独立成 Tab
- services 状态移到 Header
- memory 作为 agent Tab 内的视图
- agent-tools 作为 agent Tab 内的子面板

### 已完成
- [x] Tab 定义更新为 4 个 (experiments | workflow | agent | images)
- [x] 移除 jobs, services, agent-tools, memory 独立 Tab
- [x] Header 添加 Services 状态区 (LabRAD/Ray/DataVault 状态点)
- [x] Sidebar 动态渲染框架
  - experiments: JobManager
  - workflow: WorkflowHistorySidebar
  - agent: ChatHistorySidebar
  - images: 空
- [x] Qubit 选择器添加搜索、参数设置⚙和删除✕按钮
- [x] Qubit 选择器只在非 images tab 显示
- [x] MemoryPanel.tsx 修复 TypeScript 错误 (colSpan)
- [x] ChatHistorySidebar.tsx 组件 (聊天会话历史列表)
- [x] WorkflowHistorySidebar.tsx 组件 (工作流历史)
- [x] AgentChatPanel.tsx 视图切换 (Chat | Memory | Tools)

### 待测试
- [ ] 前端页面测试（Tab 切换、Sidebar 渲染）
- [ ] Qubit 选择器功能测试
- [ ] Agent Tab 内视图切换测试

### 技术细节
- Sidebar 宽度: 220px
- Qubit 选择器固定在 Sidebar 底部
- ChatHistorySidebar 使用 api.listChatSessions() / api.deleteChatSession()
- WorkflowHistorySidebar 使用 api.listWorkflowRuns()

---

## 2026-09-03: 记忆与反思系统 MVP

### 已完成
- [x] `scripts/memory_store.py` - 记忆存储模块 (Episode + Skill CRUD)
- [x] `scripts/reflection_engine.py` - 反思引擎 (LLM驱动的反思分析)
- [x] `scripts/job_runner.py` 集成
  - `_get_memory_store()` / `_get_reflection_engine()` 延迟导入
  - `_run_agent_chat_with_memory()` 带记忆召回和反思的任务执行
  - `recall_memory` 工具注册
  - 记忆上下文注入到 ReAct prompt
  - 8个记忆 API handlers
- [x] `src/index.ts` - Express 路由 (8个 /api/agent/memory/* 端点)
- [x] `src/lib/api.ts` - 前端 API 客户端 (8个 memory* 方法)
- [x] `src/components/MemoryPanel.tsx` - 记忆中心 UI (3个 tab: 任务记录/技能库/统计)
- [x] `src/app/page.tsx` - 集成 MemoryPanel tab

### 待测试
- [ ] 后端服务启动测试
- [ ] 前端页面测试
- [ ] 完整任务执行 -> 记忆存储 -> 反思流程测试

### 技术细节
- 存储: JSON文件 (`config/memory/episodes/` 和 `config/memory/skills/`)
- 反思触发: 任务完成后自动调用 `reflect_after_task()`
- 技能提取: 反思报告建议创建时自动生成
- 记忆召回: 任务开始前 `recall_for_task()` 匹配相关技能和历史


---

## 2026-09-24  C 组真机接通（只读联调）落地

- **范围**：feat / fix / test / docs（分支 `feat/c-real-machine`，7 个提交）
- **改动**：
  1. LabRAD 连接参数外置：`get_labrad_params` / `labrad_connect` / `get_labrad_paths`，10 处 `labrad.connect()` 全部替换
  2. 修复离线↔在线切换：懒连接、清 `DEGRADED_PERMANENT`、`/mode` 真实返回 `connected`；`default_mode: auto`
  3. 写闸门 `actuation`（默认全关）+ `/health` `/status` 增补 `data_source` / `actuation_allowed` / `labrad_host:port`
  4. 执行超时标记 `stuck` 并拒绝后续下发（BACKLOG B4）
  5. agent 在线图源切换 + 工具层 `read_only` hint 透传
  6. 14 个单元测试 + `check_readonly_regression.py` + `check_labrad_link.py`
  7. `docs/REAL_MACHINE.md` 运维手册；CLAUDE.md 写闸门约定
- **文件**：
  - `services/common/config.py`、`quantum_service/{server,labrad_client}.py`、`analysis_service/server.py`
  - `agent_service/{adapter,server}.py`、`common/quantum_tools/execution.py`
  - `config/{system,fallback_config}.json`、`tests/test_real_machine_unit.py`
  - `docs/{REAL_MACHINE.md,deploy_diff_tools/check_labrad_link.py,BACKLOG.md,REAL_MACHINE_PLAN.md}`
  - 另有 gateway / workflow / scripts / backends 的 `labrad_connect` 接线
- **验证**：
  - 单元测试 14/14 PASS
  - 活体只读回归 ALL CHECKS PASSED（13/13）：闸门关、`set_params`→`read_only`、离线模拟不受闸门限制
  - `check_labrad_link.py`：3 项 FAIL 均为预期缺口（无 manager / 无 `~/.lqcs` / connect 连不上）
- **提交**：`fe73642` → `5a0fc90` → `209f494` → `a1a4e5e` → `8079bf3` → `2bd4dac` → `7b65bd3`

## 2026-09-24  服务启动与验收

- **范围**：chore（用户授权代启服务）
- **改动**：启动 Python 微服务（3003–3011）、Express（3002）、Next.js（8081）；跑验收脚本
- **验证**：活体只读回归 13/13 PASS；前端 200；`quantum: mode=auto data_source=offline actuation=False`
- **备注**：默认仍遵守约定 3「服务启停由人控制」；本次为用户明确授权例外

## 2026-09-24  约定：每次修改必须记录日志

- **范围**：docs（约定）
- **改动**：用户要求「以后每次修改都要记录进日志」。本文件为开发日志入口，**每次修改后追加一条**
- **文件**：`CLAUDE.md` 约定 5、`.claude/progress.md`

---
## 2026-09-29  P0 本机接线闭环 + 服务验收（前端用户已确认）
- **范围**：chore / fix（分支 `feat/c-real-machine`）
- **改动**：
  1. vendor 三模块落位 `QMClaw\\vendor\\`（hermes-agent / QubitClient 0.6.22 / QCA）
  2. qca_service 两处 `D:\\Documents\\QMClaw` 硬编码改为相对 `__file__` 推导（server.py / tools/lab_tool.py）
  3. 离线数据 5011 hdf5 拷入 `qmclaw-server\\data\\offline_data` 并生成索引（qubits 111 / datasets 5008 / experiments 13）
  4. `.env` 落位（PYTHON_BIN 指 deploy 环境、PORT=3002；密钥不入库）
  5. qmclaw-web / qmclaw-server `npm install`；4 个包 `pip install -e` 指向新路径
- **验收**：
  - `check_services.py` 10/10 可导入（qca 由 FAIL → OK）
  - 服务实启：quantum:3003 / analysis:3004 / Express:3002 / Next.js:8081
  - `GET /health` → express ok + 微服务清单；前端 HTTP 200
  - **用户浏览器验证前端页面通过**
  - 无 LabRAD 时 quantum 自动降级离线（auto 模式），加载 5008 datasets
- **遗留（待决策）**：
  - pip check：`openai 2.54.0` / `rich 14.3.4` vs hermes-agent pin
  - qca 自有 `tools/lab_tool.py` 被 vendor 同名包遮蔽（含悬空 `from .runner import`），当前不可达
  - `config/qubitclient.json` 是否需要由人工配置
- **提交**：本条 progress 记录

---
## 2026-09-29  B 组线程安全落地（B2→B3→B1）+ 服务重启验收
- **范围**：fix（分支 `feat/c-real-machine`）
- **改动**：
  1. B2：新增 `services/common/plotting.py`（PLOT_LOCK / @serialized_plot / plot_lock()）；analysis 6 个绘图入口 + quantum `_generate_simulation_plot` 全部串行化
  2. B3：`memory_store` 加 `_INDEX_LOCK` + `_update_index()`；create/archive/create_skill/delete 四处索引写路径原子化
  3. B1：`base_service` `HTTPServer` → `ThreadingHTTPServer`；慢请求（默认 5s）打 `SLOW` 日志；`request_count` 改为只计真实请求
  4. 修复：`services/common/config.py` 启动时 `load_dotenv(qmclaw-server/.env)`——原先 Python 侧只读 os.environ，.env 里的 LLM Key 读不到
- **施工**：dsh 派单 T11（沙箱不能写 D 盘，产出 T11.patch）→ MiMo 应用 patch 并逐条复核
- **验收**：
  - `check_services.py` 10/10，exit 0
  - 绘图锁/索引锁/ThreadingHTTPServer 源码与 import 实测通过
  - dsh 并发实测：memory 索引孤儿 316→0；快请求被堵 1.2s→0.02s
  - 服务重启后：3002/3003/3004/8005/3006/8081 全监听；memory/stats 经 Express 返回正常
  - llm_service 读到 DEEPSEEK_API_KEY（SET）
- **遗留**：
  - `analysis_service._handle_execute` 注入 plt 未加 PLOT_LOCK（刻意，避免卡死拖死绘图）
  - image :3007 / qubitclient 等可选服务仍未启；OPENAI/ANTHROPIC/MINIMAX Key 未配
  - BACKLOG A 组（信号列/T1 拟合）仍待导师确认
- **提交**：本条记录 + B 组代码

---
## 2026-10-08  导师上游融合方案（方案已就绪，**合并尚未执行**）
- **范围**：docs（方案 + 复核 + 校对）；**未改动任何源码**
- **改动**：
  1. 定源：导师上游是 `Secsix/QMClaw`（main=`03d838d`）；`f2d83b5` 是**本仓库 PR #2 的合并提交**、不在上游历史里。fork 点靠**内容相同**确认（上游 `c3fd6e6` 的 tree 与 `f2d83b5` 的 tree 哈希相同）
  2. 量化：我方 36 提交 / 62 文件；导师 2 提交 / 41 文件；**重叠 11 文件 = 7 自动合并 + 4 冲突（共 6 处）**
  3. 冲突解法做成可运行脚本 `docs/merge_verify_tools/resolve-conflicts.py`（默认试算，`--write` 才写回），规则见方案第五节
  4. 归档 12 个验证脚本 + `README.md`（含 8 个实测过的坑）+ `ARCHIVE_CHECK.md`（哈希校验）
  5. BACKLOG 新增 **G 组 10 条**（记忆三处收敛 / skills 修法 / 依赖未声明 / 状态文件入库 / 前端 4 依赖 / offline_data_lab 回归 / qca 死代码 / tsbuildinfo / SSE）
  6. `.gitignore` 加了 3 条临时目录规则（**未提交**；第 0 步会先还原成 HEAD 版再提交）
- **验收**：
  - 真实 `git merge` 重放：冲突 4 文件 / 6 块，与预判**逐项一致**
  - 端到端流水线：`merge exit=1` → `resolve exit=0` → 后处理 3 提交 → **13 PASS / 0 FAIL**
  - 方案 32 条事实主张机器复核：**0 失败**
  - 独立对抗式复核 + 逐字校对共修复 **13 处必改 / 3 处建议改**（含 2 条"照抄会失败"的真问题）
- **遗留（合并时办）**：
  - 3 个待用户拍板：技能目录修法（建议 `mklink /J`）、`memory/MEMORY.md` 与 `auth.lock` 入库决定、`.env` 的 `HERMES_HOME` 语义
  - 第 0 步必须**同时提交 3 个已修改文件**（漏 `docs/BACKLOG.md` 会被 0.1 的断言卡住）
  - 第 9 步**保留 `.upstream-mirror/`**（12 个归档脚本里 5 个依赖它）
- **提交**：本条记录（**尚无合并提交** —— 方案见 `docs/UPSTREAM_MERGE_PLAN.md`）

---
## 2026-10-08（续）  融合方案收尾：一键执行脚本 + 结果预览 + 第 0 步 bug 修复
- **范围**：docs / `.claude` 仅文档；**未改动任何源码**
- **改动**：
  1. 新增 `docs/merge_verify_tools/run-merge.ps1`（纯 ASCII、15.4 KB）：把第六节第 0~9 步做成一条命令，含**人工闸门**；`-DryRun` 可在探针仓库上演练，`-Continue` 做快进合回
  2. 新增 `MERGED_CONTENT_PREVIEW.md`：把 4 个冲突文件**会被解成什么样**从探针合并提交导出（模型 10 条 + 默认模型、装饰器 AST 绑定、并集结果、整体改动面）
  3. `CLAUDE.md` 顶部加入口小节（指向方案 / 预览 / 一键脚本 / BACKLOG G 组 + 待确认 3 项）
  4. 归档 README 的坑清单补到 **10 条**（新增 `.gitignore` 忽略规则、`cmd` 吃 `^`/`--` 两条实测教训）
  5. 第 0 步改为**动态**：不再写死"已修改文件清单"，改为 `git status --short --untracked-files=no` + `git add -u`
- **修掉的 bug（本轮实测发现）**：
  - `run-merge.ps1` 第 0 步原用 `git add <路径>`，在"工作树脏"路径上会被拒绝：
    `.gitignore` 含 `.claude/`，对**尚未入索引**的路径 `git add <path>` 会判 "ignored"。
    真实仓库"恰好"能过只因 `progress.md` 本就已跟踪（`git check-ignore` 对它退出码 1）。
  - 改为 `git add -u` 后实测：`.gitignore` 还原 603 字节 → `add -u`/`commit` exit=0 → 提交后脏文件 0 →
    随后真实 merge `CONFLICT ×4`（与预期一致，未 Aborting）
  - 之所以一直没发现：`-DryRun` 每次都从干净状态起步，**第 0 步的"脏"分支从未被走到**
- **验收**：32 条事实主张复核 0 失败；端到端流水线 `4 冲突 → resolve → verify` 全绿；13 个归档脚本哈希一致
- **遗留**：同上一节（3 项待拍板）；第 0 步现在会自动适配文件数变化，无需再手工核对清单
- **提交**：本条记录（**仍无合并提交** —— 执行需用户在沙箱外授权运行 `run-merge.ps1`）

---
## 2026-10-08  ★ 导师上游融合【已执行完成】
- **范围**：用户把会话切到完全权限后执行（此前 11 轮受沙箱禁写 `.git` 阻塞）
- **执行方式**：`docs/merge_verify_tools/run-merge.ps1` 第 0~9 步
- **提交序列**：
  | 提交 | 内容 |
  |------|------|
  | `3d646c9` | 第 0 步：提交 5 个已修改文件（`.gitignore` 先还原成 HEAD 版） |
  | `bf725e9` | **合并提交**（父1=`3d646c9` 我方，父2=`03d838d` 导师上游） |
  | `547a0d0` | 停止跟踪构建产物 `tsconfig.tsbuildinfo` |
  | `7e09c8e` | 停止跟踪运行时残留 `auth.lock` |
  | `72cf997` | 忽略上游融合临时目录（第 8 步） |
  | `bdafd81` | 融入方案 / 复核 / 校对报告 + 13 个验证脚本归档 |
- **执行中的实况与偏差**：
  1. **冲突 4 个文件、与预判逐项一致**：`model_configs.json`(1) / `base_service.py`(1) / `HermesChatPanel.tsx`(3) / `CLAUDE.md`(1) = 6 处
  2. 合并改动面 `41 files changed, +12400 −357` —— 与 `MERGED_CONTENT_PREVIEW.md` 的预测**逐字吻合**
  3. 我方 36 个提交全部保留（`f2d83b5..bf725e9` = 40 个提交，历史未被改写）
  4. **执行中修掉 `run-merge.ps1` 的两个 bug**（当初演练覆盖不到的分支）：
     - git 把 CRLF 警告写到 stderr，`$ErrorActionPreference='Stop'` 下被当成终止错误 → 脚本中途退出。
       已在 `GitEx` 内局部设为 `Continue` 并按退出码判定；其它 6 处内联 `& git` 也统一走 `GitEx`
     - `git fetch` 递归抓子模块失败（`vendor/` 三个是无 `.git` 的普通目录）→ 退出码 1 但**上游 ref 其实已取到**。
       已改为 `--no-recurse-submodules`，并以 **ref 是否存在**判定成功，而非只看退出码
- **验收（合并后真实仓库）**：`probe-verify` **12 PASS / 0 FAIL**（4 SKIP 需人起服务、3 WARN 为待决/误报）
  - `#14 .gitignore 规则齐备` 由 FAIL → PASS；`#19 tsbuildinfo 出库` PASS
  - `#4` 装饰器 AST 绑定正确（`DELETE → do_DELETE`）；`#2` 模型 10 条、首条 `deepseek-flash`
  - `#18` 报的 8 处"旧引用"经核实是**误报**（全在 `CHANGES_VS_SOURCE.md` / `GIT_HISTORY.md` / `TODO_RUNTIME.md` 的历史日志里）
- **3 项决策的处置**：
  1. 技能目录 → **已建目录联接** `Agentic Workflow\qmclaw-server\data\skills` ⇄ `<repo>\skills`；
     实测 `rglob('SKILL.md')` 扫到**全部 7 个技能**（此前该目录不存在 → `/api/hermes/skills` 必然为空）
  2. `auth.lock` → 已出库 + 加 `.gitignore` 精确规则（`*.lock` 会误伤 `package-lock.json`，故不用通配）；
     `memory/MEMORY.md` → **保留入库**（内容是与图表方案一致的 `/plots/` URL 规范）
  3. `.env` 的 `HERMES_HOME` → **保持现状**（核实：`skills.py` 根本不读该变量，改它修不了技能列表）
- **仍待人工（约定 3：服务启停由人控制）**：验证清单第 7 项（`npm install` + 前端构建）、
  第 8/9/10 项（起服务跑接口与端到端 t1、肉眼比图）、第 11/23 项（`/api/hermes/skills` 非空）
- **回退点**：`backup/pre-upstream-merge-20261008-202936`（合并前状态）
  ```powershell
  git reset --hard backup/pre-upstream-merge-20261008-202936
  ```

---
## 2026-10-08  融合后服务启动验收 + **修掉我引入的默认模型回归**
- **范围**：chore（用户明确授权代启服务，同 2026-09-24 的例外）/ fix / docs
- **服务启动（11 个，全部 UP）**：
  - 10 个 Python 微服务 3003–3012（`scripts/start_all_services.py --all`，后台作业）
  - Express 网关 :3002（`npm run dev`）→ `{"express":"ok","mode":"microservices"}`
  - Next.js 前端 :8081 → HTTP 200（标题 `qmclaw - Quantum Measurement & Calibration Workflow`）
  - **前置**：先 `npm install` 装了合并带来的 4 个前端依赖（`highlight.js`/`react-markdown`/`rehype-highlight`/`remark-gfm`，
    装前 `require.resolve` 全部 MISSING，装后全部 OK；`package-lock.json` 未被改动，added 105 packages）
- **★ 发现并修复一处我引入的回归（`eb65542`）**：
  - **现象**：`/api/models` 首条不是 `deepseek-flash`，而是 `model_default_002`（GPT-4o Mini）→ 推翻 H3 验收
  - **根因两层**：① `src/services/modelService.ts:204` 的 `listModels()` **按 `updatedAt` 倒序排序**，
    所以"把 deepseek-flash 放 JSON 首位"根本不起作用；② 我在解 model_configs 冲突时"采纳导师字段"
    把导师那批 `createdAt/updatedAt`(2026-09-23) 也一起采纳了，比我方的 2024-01-01/2026-09-21 新 → 排序整体翻转，
    `deepseek-flash` 从第 1 掉到第 9
  - **实测**：导师对同名模型**只改了时间戳**，唯一实质改动是 `MiniMax-M2.7` 的 `baseUrl → .../anthropic`
  - **修法**：同名逻辑模型保留我方 `createdAt/updatedAt`（纯元数据），只采纳实质字段
  - **复核**：修复 diff 只动时间戳（16 行），`baseUrl` 导师改动保留；`/api/models` 与 `/api/hermes/models` 首条均恢复 `deepseek-flash`
- **升级校验器（`68340d3`）**：`probe-verify.py` 第 2 项原只看**文件里**首条 enabled+text，
  而 handler 取的是**按 updatedAt 倒序后**的首条 → 盲区。已改为两项都打印、以排序后为准。
  **反向测试**证明判据有效：修复态 PASS、回归态 FAIL（且回归态下"文件首条"仍是 deepseek-flash，正是旧判据漏判的原因）
- **验收**：
  - 前端构建 `npm run build` → **`✓ Compiled successfully`**，类型检查通过，4 个静态页生成（验证清单第 7 项）
  - `/api/hermes/skills` → **非空**，返回 7 个真实技能（技能目录联接生效）
  - `/api/agent/memory/stats` → `episodeCount:14, orphanCount:0`（我方 memory 功能可用）
  - `POST /api/hermes/chat` → `completed:true`，回复 `OK`（挂死修复仍生效）
  - 交付报告自身 **27 条事实核验 0 失败**
- **交付物**：`docs/UPSTREAM_MERGE_DELIVERY.md`（对比改动前的详细交付报告）
- **仍待人工**：浏览器验前端页面、第 9 项端到端 t1 + **肉眼比图**（`offline_data_lab.py` 被上游整份重写）、第 11/23 项页面看技能列表
- **提交**：`eb65542`（修复回归）→ `68340d3`（升级校验）→ `c1bfd44`（交付报告）

---
## 2026-10-08  归档 dsh 工单 T11（分级记忆开工前的干净基线）
- **范围**：chore（约定 11：动存储格式前先提交回退点）
- **改动**：`.dsh-orders/T11.json` 入库归档（B 组线程安全改造工单，含 scouted/constraints/acceptance，无密钥）
- **背景**：分级记忆管理方案与详细 todolist 已产出并与本文件做了差异比对（复用 Hermes 会话 UI/cron、补 `_annotate_episode` 锁外旁路、G3 schema 分叉等修订点），即将按 Phase 0 开工
- **验证**：`git diff --cached` 仅含 T11.json；密钥扫描无命中
- **提交**：本条记录 + T11 工单

---
## 2026-10-08  分级记忆管理落地（T1–T7，brain-worker-team 派单）
- **范围**：feat(memory)（分支 `feat/c-real-machine`）
- **架构**：五级记忆模型——L0 工作记忆 / L1 会话 / L2 情景 / L3 技能 / L4（待 G1 收敛，本期不动导师侧）；
  召回 = 关键词+元数据打分（qubit+实验类型+失败教训+时间衰减）+ 字符预算 + `recall_meta` 可解释；存储保持 JSON 文件
- **改动**：
  1. T1 `services/common/memory_store.py`：`create_episode` +session_id/round_index/model/response（实体+索引原子写）；
     `update_episode_fields`（受锁 RMW）；`list_sessions`/`get_session_episodes`（空 task 过滤、老数据单轮退化、roundIndex/timestamp 双排序）
  2. T2 `services/agent_service/server.py`：`_annotate_episode` 直写旁路收编进受锁路径（B3 遗留清零）；
     `POST /memory/sessions`、`GET /memory/sessions/{id}`（user/assistant 回放映射）
  3. T3 `src/index.ts`：两条 Express 会话路由（+12 行，tsc 错误数持平）
  4. T4 `AgentChatPanel.tsx` + `api.ts`：sessionId/round_index 状态、会话侧栏（照 Hermes 模式）、刷新恢复、老数据退化入口
  5. T5 L0 工作记忆：`_working_memory` 窗口（保首轮+最近 10 轮）+ 中段折叠压缩（纯截断不调 LLM）+ TTL 惰性淘汰；
     `config/system.json` 增 `memory` 块（window_size/summary_threshold/session_ttl，缺省安全）
  6. T6 `services/common/memory_manager.py`（新）：分层召回门面，`match_skills` 命中数+成功率排序，
     `recall_for_task` 形状兼容、`/memory/recall` 透传 `recall_meta`
  7. T7 episode `lifecycle(active|archived|pruned)`+`promoted_to`（缺省兼容老数据）、`run_maintenance` 归档/清理
     （实体+索引同删保 orphanCount 语义）、`POST /memory/maintenance/forget`、反思提升链收紧（仅 success 升技能，
     回标 promoted_to，顺带修掉 skillCreated 从不落盘的老 bug）、**G3**：`scripts/memory_store.py` 收敛为转发层（单实现）
- **验证**：pytest 全量 **59 passed**（基线 21 → 59；新增 5 个测试文件 37 用例，含 10 线程并发不丢索引）；
  web `tsc --noEmit` **EXIT=0**；server tsc 错误数不增（32 持平）；真实 `config/memory` 14 episode 全程未动；
  Lead 对 7 单逐单 `git diff` 逐行审 + 亲自实跑 acceptance（1 个 must-fix 已打回闭环：空 task 过滤口径）
- **施工方式**：brain-worker-team——Lead 拆单/侦察/对抗式审查，deepseek-flash 打手经 `workflow.agent()` 派单。
  **实测结论**：`spawn_teammate`/`subagent` 均无 model 字段且子代理继承主模型（mimo），`agent-default-model` 无效；
  `workflow.agent()` 的 provider/model 覆盖是本构建唯一便宜打手路由（子代理自报 deepseek-flash 验证）
- **遗留（已登记 BACKLOG H 组）**：summary_threshold 语义空挂；update_episode_fields 不回写 index 摘要；
  单轮退化种子消息仅前端内存；42 个 pytest-cache-files-* 空目录删不掉（目录授权表异常，ACL 技能判定 NOT_THIS_CLASS）
- **待人工**：端到端验收需重启服务（约定 3）——当前全部服务未运行（0 监听）
- **提交**：`feat(memory)` ×2 + `docs(memory)` ×1

---
## 2026-10-13  分级记忆端到端验收（全绿）+ 记忆目录权限修复
- **范围**：fix / chore（用户授权启动服务验收）
- **验收结果**：
  1. **会话分组（F 验收核心判据）**：2 轮对话 = 1 会话 2 轮（`rounds=2`，不是 2 个单轮）；`roundIndex` 1→2 连续；回放 4 条消息（user/assistant ×2）
  2. `/memory/stats` 带 lifecycle、`/memory/recall` 带 recall_meta（tiers/预算/截断）、`/memory/maintenance/forget` 经网关安全空转——全通过
  3. 前端 :8081 HTTP 200；验收测试 episode 已归档清理（14→16→14，orphan 0）
- **e2e 抓到并修掉两个问题**：
  1. **Express 缺 `/api/agent/memory/maintenance/forget` 代理路由**（T7 工单 write_scope 不含 index.ts 的集成缝隙）→ Lead 3 行微调补上、重启网关复验通过
  2. **`config/memory` 目录树对所有人不可写**（手工交付拷贝的权限残留：`memory`/`config`/`qmclaw-server`/`Agentic Workflow` 四层缺「完全控制」授权，
     episode 落盘链断在索引写入 `[Errno 13]`）→ `diagnose-windows-sandbox-acl` 技能定点诊断 PRECONDITION → 补 4 条授权并验证
     （备份/还原命令存 `D:\semester4\.acl-recovery\`）；对照探针确认**非受限可写、沙箱受限 token 仍受限** → 微服务改以非受限方式启动后落盘正常
  3. 同一权限修复顺带解决 BACKLOG H4：42 个 pytest-cache-files-* 积灰目录 **42/42 删除**
- **服务状态**：10 微服务（非受限）+ Express :3002 + Next.js :8081 全部运行中
- **提交**：`fix(gateway)` + 本条记录

---
## 2026-10-14  Hermes 技能接线 + agent 图表修复（brain-worker-team 派单）
- **范围**：feat / fix（分支 `feat/c-real-machine`）
- **诊断方式**：两个用户报障 → 派 **4 个只读子代理**（每个问题各 1 静态链路 + 1 运行时实证），Lead 逐条复核关键代码行
  1. **Hermes 技能**：Web 读 `<repo>/skills`（Express `getHermesSkillsDir()` / `data/skills` 联接），agent 读 `$HERMES_HOME/skills` = `data/hermes/skills`（**空目录**）→ 对话里 0 技能；`agent.log` 里 `skills_list completed (101 chars)`（= 空结果 JSON）
  2. **agent 无图**：**断点①**（当轮）agent 侧写闸门在调用服务**之前**拦死（`execution.py:88/:267`），而服务端 `quantum_service/server.py:865` 明确豁免离线；**断点②**（回放）10-13 分级记忆的会话回放链路 `charts` **三层全缺**
  3. **推翻 G7 假设**：`offline_data_lab.py` 重写**不是**原因——今天生成的 `temp_*.png` 与 09-23 参考图 **SHA256 逐字节相同**
- **改动（7 个文件 + 4 个测试文件）**：
  1. **A1** `scripts/setup_hermes_skills.py`（新，340 行）：幂等自举——把 `<repo>/skills` 写进 `$HERMES_HOME/config.yaml` 的 `skills.external_dirs`（vendor 原生机制，external 技能**只读**），并修复 `data/skills` 联接；不碰 vendor/上游
  2. **A2** `scripts/start_all_services.py`（+59）：启动前调用自举，失败不阻断；`QMCLAW_BOOTSTRAP_ONLY=1` 可只跑自举；**Lead 微调**：不再打印 API Key 值（改 `(set)/(not set)`）
  3. **B1** `quantum_tools/execution.py` + `agent_service/adapter.py`：两个工具加 `is_offline`（**默认 False 保真机安全**），adapter 用 `/health` 的 `data_source` 注入；**在线+闸门关仍 FORBIDDEN**
  4. **B2** `agent_service/server.py` + `AgentChatPanel.tsx`：episode 用 `update_episode_fields` 补写 `charts`+`dataset_id`；回放 assistant 消息带 `charts`；前端 `sessionToMessages` 两分支映射
  5. **B3** `agent_service/server.py`：`_experiment_outcome()` 纯函数 + `metrics.experiment_executed`/`blocked_reason`（**status 语义不变**）
  6. 测试：新增 `tests/test_agent_offline_gate.py`(14) / `tests/test_agent_episode_charts.py`(9)；`tests/test_agent_sessions.py` 一处「metrics 精确等值」改**按键断言**（Lead 修，顺带覆盖新键）
- **验证（Lead 亲跑，不信打手自述）**：
  - pytest 全量 **82 passed / 0 failed / 0 skipped**；web `tsc --noEmit --incremental false` **EXIT=0**
  - **live**：Hermes 对话 **12 秒列出全部 7 个技能**（含 description）；agent 离线跑 t1 → **`charts=1`（80KB PNG）** + `dataset_id=20251103_00398` + `metrics T1(µs)=8`，且**闸门仍关闭**（离线豁免生效）；回放 assistant `charts=1`、episode 落盘一致
  - 自举脚本：真实路径 `no change` + `junction ok`（幂等）、短路模式 EXIT=0 且**不启动任何服务**
- **新发现（已登记 BACKLOG I5，P1）**：**并发 `/execute` 使服务挂死**——19:56 两个并发离线实验服务端都跑完出图，但响应双双未回，此后 quantum `:3003` 与 hermes `:3012` 全无响应（TCP 可连、HTTP 无响应），重启后恢复；**与 B1（单线程 HTTPServer）不同源**
- **施工方式**：brain-worker-team（Lead 拆单/侦察/对抗式审查/亲自 live 验收；打手为 deepseek-flash——本机 `settings.yaml` 缺失使 `subagent` 显式模型选择被禁用，靠「Lead=flash → 子代继承」达成，见 BACKLOG I6）
- **提交**：`feat(hermes)` + `fix(agent)` ×2 + `docs`

---
## 2026-10-30  沉淀 skill：lqms-offline-datavault-plotting（离线 DataVault 出图）
- **范围**：feat / docs（只新增 skill 与索引登记；不触碰测控链路、不启停服务）
- **来源**：继承 Codex 会话 `01a185d6` 后完成 9 张图（342~350）的过程中踩的坑，逐条固化成 skill
- **改动（4 个文件）**：
  1. **A1** `skills/lqms-offline-datavault-plotting/SKILL.md`（新）：读 HDF5 → 重建 `dc.DataLab` → 参数别名 → 分析分派 → 出图 → 矢量导出的完整链路，含 9 条错误速查、6 条已知数据陷阱
  2. **A2** `skills/lqms-offline-datavault-plotting/scripts/plot_datavault.py`（新，460 行）：按文件名自动分派实验类型、批量出图、PNG/SVG/PDF 三格式 + 多页合订；lqms 入口失败自动转自绘兜底
  3. **A3** `skills/lqms-offline-datavault-plotting/README.md`（新）：skill 卡片
  4. **A4** `README.md` / `install.md`：skill 索引各加一行（+ 目录树一行）
- **验证（实跑，非推断）**：
  - `LQHL/test/20260324` 全 session **738 个数据集批量出图，失败 0 / 738**（中途两轮修复：13 → 4 → 0）
  - 9 张目标图（342~350）三种格式齐出；SVG 逐个校验 `<image>` 计数 = 0（纯矢量）；合订 PDF 逐页校验内嵌位图 = 0
  - 兜底路径实测生效：9 个单模 IQraw（依赖量仅 `Is|0`/`Qs|0`）→ lqms `IQ_raw` 抛 `IndexError` → 自绘 I/Q 散点；4 个二维 T1（zpa×delay）→ `dp.T1` 抛 `zpa2freq`/`GridData` 错 → 自绘热图 + 逐 zpa 单指数拟合
- **沉淀的关键结论**（此前无文档）：
  1. lqms 自带 local vault 初始化依赖 LabRAD/Registry 认证，离线必须用 `lv.dataset_loader_hdf5` 直接读
  2. `dc.DataLab` 离线重建的关键字段：`datasets_num` 必须 list、`data_shape` 必须 dict、`name_info` 必须带 `q_name`、`measure_info` 必须带 `units`
  3. ind/dep 表项要同时是 dict（带 `units` 与 `unit` 两个键）且有 `values()` → 自定义 `Header(dict)`
  4. `q3ld4.*` 参数需在内存里补 `q.*` 别名，否则 `ramsey`/`dispersive_shift`/`IQ_raw` 报 `KeyError: 'q.f10'`
  5. `fitFunc.find_peaks` 返回 dict（`peaks`/`dips`/`max point`/`min point`），不是数组
  6. **矢量导出**：`imshow` 与 `pcolormesh` 的色标条会在 SVG/PDF 内嵌位图，2D 图须用 `contourf` 才纯矢量

---
## 2026-10-30  S21 截图读数工具（比特号 → 波谷值，可复用）
- **范围**：feat / docs（新增离线工具 + skill；不触碰测控链路、不启停服务）
- **来源**：用户要求识别 `D:\QMClaw\s21_image\读取线6上的待测比特.png`（S21 find dips 截图），
  输出比特号与对应波谷值；并明确"后续还会有其他类似照片"→ 把一次性手工流程固化成通用工具
- **一次性结果**（已交付 `s21_image\s21_dips_R6.csv` / `.md`）：
  - 图上标注的数字 = **波谷所在频率（GHz）**，非幅值；逐点与 X 轴刻度校验（误差 < 1 px）
  - 9 个比特：q15_11 6.2835 / q13_13 6.353 / q17_9 6.393 / q11_15 6.445 / q19_7 6.469 /
    q9_17 6.504 / q5_21 6.5465 / q21_5 6.556 / q7_19 6.6215
  - 另附栅格读出的波谷幅值（Amplitude 0~140000，误差 ±5~10%）
- **改动（12 个文件 + .gitignore 1 行）**：
  1. `tools/s21_photo_reader/s21pr/`（新，9 模块）：`ocr` / `plot`（图框+坐标轴标定+文本分类）/
     `curve`（曲线提取、波谷与基线测量）/ `recovery`（局部放大二次 OCR）/ `pairing`（单调 DP 配对）/
     `pipeline` / `report`（CSV+MD+标注核对图）/ `cli`（单张与批量）
  2. `tools/s21_photo_reader/run.ps1`（新）：一键运行（自动找解释器 + UTF-8）
  3. `tools/s21_photo_reader/README.md`（新）：原理、参数、依赖、边界
  4. `tools/s21_photo_reader/tests/`（新）：合成图生成器 + 回归测试
  5. `skills/s21-photo-reader/SKILL.md`（新）：供 agent 复用的技能卡
  6. `.gitignore`：`.pylibs/`（206MB OCR 运行时，不入库）
- **验证（实跑）**：
  - 真实照片：9/9 比特名与波谷值全部还原，且与人工逐像素分析结果一致（波谷幅值偏差 < 1%）
  - 合成回归测试（不同频率范围/比特名/排布）：**PASS**，5/5 全对
  - 批量模式：目录 2 张图 2/2 成功，汇总 CSV 正常
- **施工中修掉的坑（值得记住）**：
  1. **曲线陡直段会被当成网格线删掉** → 网格线判据改为"贯穿整个绘图区"（上下端贴边）
  2. **Y 轴标定方向反了**（拟合得到 pixel=f(value)，取幅值时未取逆）→ 波谷幅值一度全错
  3. **OCR 把小字号首位 6 读成 5**（`6.2835`→`5.2835`）→ 波谷值改以**几何位置反算**为准，
     图底列表 `[6.2835, ...]` 做交叉校验
  4. 波形"悬崖"是竖直线段，不能靠"竖直暗线"判网格线（第一版用 55% 覆盖率即误判）
  5. 合成测试第一版把比特名画在曲线上 → OCR 必然读不出（是生成器的锅，不是工具的锅）
- **提交**：`feat(s21-photo)` = `c7fc265`；`fix(s21-photo)` = `e7448b9`
  （修 `run.ps1` 编码：Windows PowerShell 5.1 按 ANSI 读 .ps1，脚本里必须纯 ASCII；
  另把误提交的测试产物 `_tmp_test/` 从版本库移除并加进 `.gitignore`）

---
## 2026-10-30  S21 读数 → LabRAD regs.fread 自动赋值（承接上一条）
- **范围**：feat（新增赋值层；不动测控服务、不启停任何服务）
- **需求**：用户给出 LabRAD 笔记里的写法 `q21_5.regs.fread = 6.556`，
  要求"以后每个照片得到的信息直接再进行 s21 赋值"
- **改动（6 个文件）**：
  1. `tools/s21_photo_reader/s21pr/assign.py`（新，~300 行，纯标准库）：
     解析 CSV / 粘贴文本 → 写 `regs.fread` → 逐条回读校验 → 生成 LabRAD 赋值脚本
  2. `tools/s21_photo_reader/labrad_assign_fread.py`（新）：LabRAD 侧入口
     （读 `s21_image` 下最新结果 CSV，用**调用方**的 globals 找比特设备）
  3. `tools/s21_photo_reader/s21pr/cli.py`：识别后自动产出 `<名字>_labrad_assign.py`
  4. `tools/s21_photo_reader/tests/test_assign.py`（新）：mock 设备回归测试
  5. `tools/s21_photo_reader/README.md`：新增"写回 LabRAD"章节
  6. `skills/s21-photo-reader/SKILL.md`：技能卡补上赋值步骤
- **关键设计**：
  - 比特对象只存在于 LabRAD 进程内 → 外部只能"备好读数"，赋值必须在 LabRAD 里执行；
    因此提供两条路：**粘贴即用脚本**（值已内联）与**读 CSV 批量赋值**（读数更新不必重新生成）
  - 与仓库既有写法一致（`vendor/QubitClient/.../mcp_tools.py` 用 `globals()[qname]` + `set_nested_attr`）
  - 只写 `regs.fread`，不碰 f10 / bias_z / fc；单条失败不中断其它比特并单列失败清单
  - 命名空间里没有该比特时明确报"找不到比特对象"，不静默跳过
- **验证（实跑）**：赋值回归 **PASS**（dry-run 不写硬件 / 真写入 9/9 / 缺失比特单列失败 /
  粘贴文本解析 / 生成脚本在 mock 设备上执行 9/9）；照片识别回归仍 **PASS**；端到端重跑正常
- **踩坑**：CLI 里新加的 `rows = assign.parse_assignments(...)` 覆盖了汇总用的 `rows`
  → 汇总表被污染（"完成: 10/1"）→ 改名 `assign_rows`
- **提交**：`feat(s21-assign)`

---
## 2026-11-04  DataVault 单比特体检报告（离线读 20260822_device1 序号 3648–4455）
- **范围**：feat（新增只读分析工具 + 报告；不连 LabRAD、不改任何硬件寄存器）
- **需求**：查看 `D:\DataVault\LQHL.dir\test.dir\20260822_device1.dir` 中序号 3648–4455 的
  昨夜测试数据，总结各比特单比特性质，并给出"活比特"（iqraw>0.1 且 piamp 实验正常）的
  **zpa / f10 / f21 / fread**
- **改动（新目录 `tools/qubit_health_report/`，5 个文件）**：
  1. `dvread.py`：DataVault HDF5 离线读取——文件名解析、`Param.*` 的 LabRAD tagged data
     解码（urlsafe base64 + 大端 float64 @偏移 11）
  2. `metrics.py`：各实验指标——IQraw(SNR/归一化分离度/保真度)、Rabi、Ramsey(T2*)、谱扫、S21
  3. `health_report.py`：主入口——按比特聚合、按工作点对齐各实验、判活、出 CSV+MD 报告
  4. `test_health_report.py`：自检 9 项（解码往返 / IQraw 可分性 / Rabi 判据 / Ramsey 已知 T2）
  5. `README.md`：用法、数据约定、判活口径、踩坑记录
  - 报告输出：`reports/20260822_device1/qubit_health.{md,csv}`
- **结论**：808 个文件 / 8 个比特 → **活比特 3 个：q13_15、q21_5、q7_21**
- **关键实现要点**：
  1. **zpa = `regs.bias_z`**（比特直流偏置），不是 `PiGate.zpa`（后者是单次 π 脉冲的 z 偏置）——
     依据 `measure_scripts/.../backend.py` 里 `sq.t1(qobj, zpa=qobj.regs.bias_z)`
  2. 本批会话是在 `(f10, zpa)` 网格上**扫描**，寄存器里的 f10 是被扫描的值（含 −201 GHz 之类越界值）
     → 参数一律取**判活那条记录**的寄存器值，其它实验按与工作点最近的文件对齐
  3. **Rabi 判据改模型无关法**（内部峰 + 峰后谷 + 落差 ≥0.15 且 ≥4σ）：噪声数据做 5 参数
     阻尼余弦拟合会发散，曾算出对比度 292、1120 这种荒谬值
  4. **Ramsey 拟合用 FFT 主频初始化**：直接拟合退化到错误频率（T2* 差 3 个量级）；
     不成立（r²<0.3）标"未测得"
  5. 谱扫 f10 = P1(freq) 共振峰；个别文件 `fread` 被误写成 182 GHz，按会话中位数修正
- **验证**：自检 **9/9 PASS**；全量 808 文件实跑 2.8 s；活比特判定在
  "IQraw 用 SNR" 与 "用归一化分离度" 两种口径下结论一致
- **提交**：`feat(qubit-health)`

---
## 2026-11-04  补充：单比特体检需求文档
- **范围**：docs（只加文档，不动代码）
- **需求**：把上面的体检需求写成 markdown 文档，标注清楚具体需求点并给出具体例子
- **改动（2 个文件）**：
  1. `docs/QUBIT_HEALTH_REPORT_REQ.md`（新）：10 节——背景/目标/数据约定/需求点索引(R1–R8)/
     详细需求点(含公式与量化口径)/**6 个具体例子**/判活结果表/边界情况与限制/验收清单/交付物
  2. `.claude/progress.md`：本条记录
- **例子均取真实数据**（非编造）：q13_15 的 IQraw 数值(sep=2063718、σ0=339310、σ1=774922、
  snr=3.450、fidelity=0.9985)、PiPulse 的 16 点 P1 序列、寄存器快照；
  q21_7 反例(序号 3861 的 P1 序列 + 3800 的 snr=0.0866)；q19_9 的 fread 误写修正
- **提交**：`docs(qubit-health)`


---

## 2026-11-12  同步分支 sync/qmclaw-20261111 —— S8：修掉 4 个上游缺陷（DSL 子系统关键增量）
- **范围**：fix（上游缺陷修复 + 回归断言落位），**不改引擎语义、不改配置红线**
- **背景**：同步计划见 `docs/SYNC_20261111_MERGE_PLAN.md`；上游 = `qmclaw-ai/sync-qmclaw-20261111`
  (`19faee0`)，合并提交 `c841983`。S0→S7 已完成，本条是 S8。
- **改动（11 个文件）**：
  1. `Agentic Workflow/qmclaw-server/services/workflow_service/qwf_store.py`（**上游缺陷 K1**）：
     新增 `REPO_ROOT_DIR` / `REPO_EXAMPLES_DIR` 与 `example_dirs()` / `example_files()` /
     `find_example()`——运行期 `data/workflows/examples/` 优先、仓库根 `qwf_examples/` 回落；
     `list_workflows()` / `read_workflow()` / `delete_workflow()` 全部改走这套解析；
     示例在服务端标为**只读**（删除会被明确拒绝）。
  2. `.../services/workflow_service/server.py`（K1）：`_handle_qwf_examples` / `_handle_qwf_to_canvas`
     复用 `qwf_store` 的统一解析；`includeText` 同时支持 query 参数。
  3. `.../services/workflow_service/dsl/canvas_ir.py`（**上游缺陷 K2**，`dsl/` 下本轮唯一改动）：
     `ir_to_canvas` 的 `workflow` 块补上 `goal` / `bounds`；`canvas_to_ir` 按 `policy`
     的现成写法把它们写回（只在非空 dict 时写回）。
  4. `Agentic Workflow/qmclaw-web/src/components/panels/QwfInfoPanel.tsx`（K2）：
     `WORKFLOW_FIELDS` 补 `goal` / `bounds` 两项。
  5. `.../qmclaw-web/src/lib/qwfBridge.ts` / `.../src/store/workflowStore.ts` / `.../src/lib/api.ts`：
     注释与文档串同步（透传逻辑本身就不用改）。
  6. `Agentic Workflow/qmclaw-server/src/services/serviceProxy.ts`：补 `GET /workflow/qwf_examples`
     （上游只有 POST），query 参数透传。
  7. `Agentic Workflow/qmclaw-server/tools/check_examples_canvas.py`：改用 `example_files()`。
  8. `qwf_examples/02_ramsey_t2.qwf` / `03_t1_fixed_zpa.qwf`（**上游缺陷 K3**）：
     `budget.max_writes` 0 → 1 + 加注释说明额度口径。
  9. `_p1_goal_dsl_check.py`（**上游缺陷 K4**）：回归 glob 修正（`qwf_examples/*.qwf` + 仓库根
     `*.qwf`）、脚本位置作仓库根兜底、新增第 7 段（K1/K2 回归断言）。**未新增检查项**。
  10. `docs/BACKLOG.md`：新增 **K 组**（K1–K4）。
  11. `docs/SYNC_20261111_MERGE_PLAN.md`：S8 补"上游缺陷修复"小节 + §6 第 4 项更新。
- **为什么 K3/K4 是真缺陷**：上游 CI 只对示例跑 **lint** 不跑 **validate**；
  且上游回归 glob 写的是仓库根 `*.qwf`，示例实际在 `qwf_examples/` → 回归段零命中。
- **验证**：
  - `run_all_checks.py`（OFFLINE）→ **39 项 / 0 失败 / 1 跳过**（`_qwf_gen_goal_check.py` 未入库）
  - `_p1_goal_dsl_check.py` → FAIL 0（含 K1/K2 新增断言：5 个示例往返全等价、`goal` 进画布）
  - `tools/check_examples_canvas.py` → 5 个示例全 `ok=True stable=True changed=False`
  - `qwf_lint.py` 对 5 个示例 → 0 error
  - `next build` → exit 0、stderr 空、`/` 252 kB / First Load 339 kB
- **未做**：不改 `dsl/validate.py` 的额度口径（引擎语义，留作后续讨论）；不改写闸门默认值；
  不起停任何服务（约定 3）。
- **提交**：`fix(dsl): 修掉同步分支带进来的 4 个上游缺陷`

---

## 2026-11-12  约定 10 解释器路径更正（用户确认）
- **范围**：docs（约定文件，只改路径一行）
- **原因**：`CLAUDE.md` 写的 `D:\semester4\deploy\qmclaw\qmclaw\python.exe` 与
  `AGENTS.md` 写的 `C:\Users\HUAWEI\.conda\envs\qmclaw\python.exe` 在本机
  **都不存在**（已实测 4 个候选路径，只有 1 个存在）；本轮同步全程用的是
  `C:\Users\lqcs\Programs\Python\Python311\python.exe`（Python 3.11.9，290 个包）。
- **改动（2 个文件）**：
  1. `CLAUDE.md` 约定 10 → `C:\Users\lqcs\Programs\Python\Python311\python.exe`
     （补注：原两个路径本机都不存在，别再按它们写命令）
  2. `AGENTS.md` 约定 10 → 同上
- **依据**：AGENTS.md 约定 5 —— 约定只保留经用户确认的内容（用户已确认"改成实际路径"）。
- **未改**：`docs/DEPLOY_LOCAL.md` 等上游文档里的 `D:\anaconda3\envs\lqcs\python.exe`
  描述的是**上游那台机器**的环境（该文件与上游逐字相同），保持原样。
- **提交**：`docs(convention): 约定 10 解释器路径改为本机实际路径`

---

## 2026-11-12  qwf 功能全面体检 → 又修掉 2 个上游缺陷（K6 / K7）
- **起因**：用户要求"检查当前项目的 qwf 功能是否正常"。写了一份 92 项断言的体检脚本
  （`_verify_qwf.py`，本地未入库，仓库根的 `_*` 被 .gitignore 排除），
  覆盖 6 层：DSL 核心 / 画布往返 / qwf_store CRUD / 服务端 handler / 根目录工具 / 全仓 .qwf 解析。
- **发现并修掉（上游缺陷）**：
  1. **K6** `dsl/textdsl/printer.py` 不打印 `decide` 的 `from` → 打印/存档/导出一次，
     决策节点就看不到上游实验数据（服务端 decision 分支只读 `node.depends`）→ 退化成"纯选择"。
     修复：`decide` 字段表加 `from`。
  2. **K7** `qwf_gen.py` 普通模式不写 `max_datasets` → 它生成的每份 .qwf 都 lint 不过
     （`E_NO_BUDGET`）。修复：budget 块始终写 `max_datasets` + 新增 `--max-datasets`。
- **记录未改**：**K8** `normalize()` 对 goal 工作流不幂等（只影响 `effect: null` 标记，
  执行语义无影响）；**K5** 传 normalize 过的 IR 当 `baseIr` 会误报改动（生产路径不受影响）。
- **顺手修**：`docs/BACKLOG.md` 里 K4 段落写的 `D:/qmclaw/*.qwf` 被
  `_docs_paths_check.py` 当成"文档里的路径必须存在"而报错（该路径不存在）→ 改成文字描述。
  （此前报过的"39 项 / 0 失败"里这一项其实是红的，这里更正。）
- **验证**：`_verify_qwf.py` → 92 项全过；`run_all_checks.py` → 39 项 / 0 失败 / 1 跳过；
  `qwf_lint.py` 对 5 个示例 → 0 error；`_verify_s8.py`（验收清单 4/5/6/7/8/12 项）→ 全过。
- **提交**：`fix(qwf): 补上 decide.from 的打印与 qwf_gen 的 max_datasets`

---

## 2026-11-12  活接口体检 + 出「给来源方的补足清单」
- **背景**：用户要求"实际缺什么，给个清单，方便与项目来源方沟通如何补足"。
  用户已在 20:24-20:25 启动了全部服务（:3002-:3011 + :8081），于是补做了**活接口**体检。
- **新增本地脚本**（仓库根 `_*`，被 .gitignore 排除，未入库）：`_verify_live.py`（活接口体检）。
- **活接口结果：20/23 通过**。qwf 全链路通：`GET /api/workflow/qwf_examples`（5 个，带文本）、
  `parse_qwf` / `validate`（0 error）/ `qwf_to_canvas` / `canvas_to_qwf`（diff 空、stable）/
  `run_ir`（dryRun，不下发硬件）/ `GET /api/workflows`（5）/ `GET /api/tasks/lane` / `stmt_to_qwf`。
- **3 处 FAIL，均已定位**：
  1. `:3010 qubitclient_service` → 503 degraded，`"No LLM configuration found in qubitclient.json"`
     → `decide`/`ask` 节点的 LLM 在本机不可用（config 被 .gitignore 排除，含内网地址与 Key）。
  2. `:3004 analysis_service` → 503 degraded，`LabRAD 不可用（已停止重连）`、`offlineAvailable:false`、
     `session_path:[]`；而 `:3003 quantum_service` 正常且 `labrad_connected:true`、会话是
     `test/20260822_device1`，与配置里的 `test/20260324` 不一致；本机也没有 `data/offline_data/`。
  3. 活服务导出的 .qwf 里 `decide.from` 仍丢 → **服务 20:25 启动，早于 printer 补丁提交（20:33）**，
     需重启 workflow_service 才生效（约定 3，由人执行）。
- **文档**：
  - 新增 `docs/UPSTREAM_GAP_LIST.md` —— 给来源方的补足清单（A 只能你们给的 5 项 /
    B 上游缺陷 7 项需回流 / C 工程卫生 4 项 / D 引擎语义 2 项待拍板 / E 我方已完成与待决 / F 验收表）。
  - 修正验收清单第 1 项路径：`GET /api/health` 是 **404**，正确路径是 `GET /health`（另有 `/api/services/health`）。
- **提交**：`docs: 新增给来源方的补足清单 + 修正验收清单路径`

---

## 2026-11-12 22:30  上游重写分支 → 第二轮合并（4fad224）+ A4 会话路径实测解决
- **起因**：用户告知上游有更新版（同一分支 `sync/qmclaw-20261111`）。
- **上游把分支重写了**：旧 tip `19faee0` → 新 tip `4fad224`（同一提交信息、同一父提交 `e3cb8fa`），
  merge-base 退回 `e3cb8fa` ⇒ 上一轮合过的文件这轮全是 add/add 冲突。
- **合并提交 `af8795d`**（备份标签 `backup/pre-sync-20261112-upstream-rewrite` = `ecf30d8`）。
  冲突处置：`.gitignore` 手工并集；`自动化单比特标定/qwf_auto_calib.py` 取上游新版（本地未改过）；
  其余**一律取本地版**（本地版 = 上游版 + K1–K8 / J 组修复）。
- **上游这次带来的变化**：`.gitignore` 放行 `_qwf_gen_goal_check.py` 与 2 份设计文档；
  新增 `_qwf_gen_goal_check.py`（141 行，**首次真跑 24 OK / 0 FAIL**）、`_p1_diff.py`、`_gen_spec_qwfs.py`、
  `docs/ARCHITECTURE_ROADMAP.md`、`docs/PLAN_PRODUCER_DEFAULT.md`、`自动化单比特标定/f10_sweep.py`；
  `qwf_auto_calib.py` 修真机坑（discriminator.center 必须数值数组 / PiGate.length 必须等于 PiHalf.length 且 20-40ns）；
  10 个 task json 出库（与本地 J8 一致）；会话路径改 `20251103_LD_SQ`；**CI 未变**。
- **B2 补丁修正**：上游新脚本自建 `argparse.Namespace` 调 `qwf_gen.build()`，直接取 `args.max_datasets`
  会 AttributeError（自检 exit 99）→ 改用 `getattr(..., "")` 兜底。
- **A4 会话路径实测解决**（用 `POST :3004/connect`，只读）：
  `20251103_LD_SQ` ❌ not found、`20260324` ❌ not found、
  `["","LQHL","test","20260822_device1"]` ✅ Connected to DataLab
  → 已改 `config/session.json` / `config/system.json`；**analysis_service 由 degraded 变 healthy**
  （`datalab_connected: true`、`data_source: labrad`）。
- **本仓库 .gitignore**：把上游仍靠 `git add -f` 强加的 4 个脚本也纳入白名单（新 clone 自洽）。
- **验证**：`run_all_checks.py` → 39 项 / 0 失败 / **0 跳过**；`_verify_qwf.py` → 92 全绿；
  `_docs_paths_check.py` → 6/6。
- **文档**：`docs/UPSTREAM_GAP_LIST.md` 按新上游逐条更新；`docs/BACKLOG.md` 新增 L 组；
  `docs/SYNC_20261111_MERGE_PLAN.md` 新增 S8′ 段。
- **提交**：`docs: 更新补足清单 + 记录第二轮上游合并与会话路径实测`

---

## 2026-11-12 22:45  用户重启服务后复验 qwf + q21_7 iqraw 链路实跑尝试
- **活接口复验**（`_verify_live.py`）：**PASS 21 / FAIL 2**。
  - ✅ **K6 修复已在活服务生效**：导出文本里 `decide ... from []` 出现（重启前是丢的）。
  - ❌ 剩 2 个 degraded：`:3004 analysis`（见下）与 `:3010 qubitclient`（缺 `qubitclient.json`，外部依赖）。
- **q21_7 存在性**：`GET :3003/qubit/params?name=q21_7` → 参数可读
  （f10=4.6092GHz / fread=6.4846 / fc=4.955 / f21=4.3892；PiGate.amp=0.8124、PiHalf.amp=0.4062、
  **PiGate.length=PiHalf.length=30.0ns** —— 满足上游新版的脉冲长度约束）。
- **qwf 链路实跑**（`_q21_7.qwf`，基于 `01_iqraw_contrast.qwf` 改 qubit=q21_7、session=20260822_device1）：
  - `parse_qwf` → IR ✅；`validate` → **0 error** ✅；`run_ir(dryRun)` → ok/irHash ✅
  - `run_ir(真跑)` → workflow_service → **task_queue（lease 模式，task_id=task_958809c9fa85）**
    → quantum_service `POST /execute` → **被写闸门拦下**：
    `status=blocked, reasonCode=read_only, "当前为只读联调，禁止向真机下发实验"`
  - ⇒ **DSL→队列→真机服务整条链路已打通**，红线按预期拦住，**未下发任何硬件指令**；
    `:3003` 未被标记 stuck、`:3009` 队列无残留。
  - 要真跑：需**人**把 `fallback_config.json` 的 `quantum_service.actuation.allow_execute` 置 true
    （或设 `QMCLAW_ALLOW_EXECUTE=1`）并重启 quantum_service（约定：开闸与重启由人执行）。
- **新发现（上游缺陷，待回流）**：**analysis_service 与 quantum_service 同时重启时**
  analysis 启动连 LabRAD 失败（LabRAD 被 quantum 占用），其重连预算是**按次数**（5 次指数退避，
  约 1 分钟内耗尽）→ 5 次全落在占用窗口内即**永久 degraded**（"已停止重连"），
  只能手动 `POST :3004/connect {"session_path": [...]}` 才恢复（本轮实测：该调用成功，health 转 healthy）。
  代码里 2026-11-02 已修过同类问题（"从未成功连接过就跳过重连"），但**按次数的预算仍有这个洞**。

---

## 2026-11-13 09:55  q21_7 的 iqraw 真机实跑 —— **成功**（写闸门由人打开）
- 09:54:54 闸门打开（用户把 `fallback_config.json` 的 `quantum_service.actuation.allow_execute`
  置 true 并重启了 quantum_service；我方的后台等待任务 `_q21_runner.py` 探到后自动开跑）。
- **qwf 全链路一次跑通**（`.qwf` 文本 → 解析 → 校验 → task_queue(lease) → quantum_service → 真机 → 读回 → 打印）：
  - `workflowId=q21_7_iqraw_1794534894195`、`irHash=sha256:18313027…`、`trace_id=t-9aeaca20b172`
  - `c1`（`sq.iqraw` on q21_7）：**真机执行成功**（`Running: 100% 1/1 [00:03<00:00, 3.26s/it]`），
    `dataset_id=4465`（session `test/20260822_device1`）
  - `r1`（`kind IQraw`）：`p1_i=0.508` `p1_x=0.5203` **`contrast=0.0123`**
    `separation=4384.6` `sigma=90990.1` `snr=0.048` `shots=3000` → `passed=false`（阈值 0.4）
  - `s1`：`say` 输出 `contrast=0.0123（数据集 4465）`
  - workflow `status=completed`、`outcome.status=achieved`；总耗时约 18s
- ✅ **上游的 discriminator.center 数值数组修复得到验证**：stdout 里
  `__setitem__ center0 to [-286559.79005146 -190988.97701544] from [-998927.28612272 -503838.7332307]`
  —— 写的是**数值数组**（上游 `qwf_auto_calib.py` 修的正是"写字符串会让厂商 iq2prob 抛 TypeError"那个坑）。
- ⚠️ **真机副作用（需知悉）**：本次实验**改写了 q21_7 的 discriminator center0/center1**（旧
  `[-998927 -503838]` / `[-329568 -522006]` → 新 `[-286559 -190988]` / `[-186321 -299385]`）；
  数据集 4465 已落到 DataVault。
- ⚠️ **contrast 只有 1.2%（远低于 0.4 阈值）** —— **不是流水线故障**：sigma(90990) 远大于
  separation(4384.6)，是"噪声压倒信号"的典型形态（两簇 p1_i/p1_x 只差 0.012）。本次 iqraw 是默认参数
  （pumpSwitch/apply_21/correctX/correctZ/reset 全 False，未做频点与读出点优化），
  下一步建议用 `自动化单比特标定/` 的 `f10_sweep.py` / `qwf_auto_calib.py` 找 f10 最优点、
  再校读出相位与幅度。
- 🆕 **新的语义观察（待拍板，已记入补足清单 D3）**：`read` 节点的 `threshold` 不达标
  （`passed=false`）时，workflow 仍然 `status=completed` 且 `outcome.status=achieved` ——
  阈值只是"标记"不"阻断"。若要硬失败需显式用 `assert` 节点。
- 本地工具保留：`_q21_runner.py`（等闸门 → 自动跑 q21_7 iqwf；`_q21_runner.py [等待秒数]`）。

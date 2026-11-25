# web/ — Next.js 控制台

平台的前端。Express 网关在 `:3002`，本目录的 Next.js 控制台默认在 **`:8081`**。

## 启动

```bash
cd web
npm install
npm run dev          # next dev -p 8081
```

打开 <http://localhost:8081>。

| 脚本 | 作用 |
|------|------|
| `npm run dev` | 开发服务器（`:8081`，热更新） |
| `npm run build` | 生产构建 |
| `npm run start` | 启动构建产物 |
| `npm run lint` | ESLint |

## 后端地址

前端默认连 `http://localhost:3002`，可用环境变量覆盖：

```bash
# web/.env.local
NEXT_PUBLIC_API_URL=http://localhost:3002
```

取值位置：`src/lib/api.ts`、`src/lib/controlApi.ts`、`src/app/page.tsx`。

## 目录

| 路径 | 内容 |
|------|------|
| `src/app/page.tsx` | 主页面：比特、实验、数据集、Agent 面板等标签页 |
| `src/app/control/` | `/control` 控制台页（状态机、QWF 流程图、作业管理） |
| `src/app/layout.tsx` | 根布局与全局样式入口 |
| `src/components/` | 30+ 个面板组件（`AgentChatPanel` `HermesChatPanel` `WorkflowDesigner` `MemoryPanel` `QiskitBackendPanel` `ServiceControlPanel` `QubitParamsPanel` `UnifiedResults` …） |
| `src/lib/` | API 客户端（`api.ts` `controlApi.ts`）、Markdown 渲染、QWF 桥接、推理块 / 工具调用卡片 |
| `src/hooks/` | 自定义 Hook |
| `src/store/` | 状态管理（Zustand） |
| `src/theme/` `src/styles/` | 主题与全局样式 |
| `src/types/` | TypeScript 类型 |
| `public/` | 静态资源 |
| `run-dev.cmd` | Windows 一键启动脚本 |

## 约定

- 后端接口一律走网关（`:3002`）的 `/api/...` 路径，**不要**在浏览器里直连微服务端口。
- 新增组件放 `src/components/`，接口调用集中放 `src/lib/`，不要在组件里写裸 `fetch`。
- 服务启停由人控制（本目录只负责前端热更新），改完前端用 `npm run dev` 即可看到效果。

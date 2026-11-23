/**
 * /control 插件页专用 API 客户端。
 *
 * 为什么不复用 `src/lib/api.ts`：那个文件是 2000+ 行的工作台客户端，
 * 插件页只用到其中很少的几个只读端点；单开一个模块让插件页的依赖面保持最小，
 * 也不必动工作台的老代码。
 *
 * 全部端点都是**只读**的：
 *   GET  /api/quantum/qubit/state-table  比特状态机全表（状态/说明/下一步/证据来源）
 *   GET  /api/quantum/qubits              当前 session 的比特列表
 *   POST /api/quantum/qubit/state         单个比特的实测状态（读执行台账，不碰硬件）
 *   GET  /api/quantum/jobs                微服务作业列表（不含 result 明细）
 *   GET  /jobs                            老队列作业列表（含 stdout / plotPath）
 *   GET  /api/workflow-runs               工作流运行记录（含节点状态）
 *   GET  /api/workflows/:id               工作流存档（含 canvas 拓扑）
 */

export const CONTROL_API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";

async function getJson<T = any>(path: string): Promise<T> {
  const res = await fetch(`${CONTROL_API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

async function postJson<T = any>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${CONTROL_API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

// ── 类型 ────────────────────────────────────────────────────────────────────

export interface EvidenceSource {
  produced_by?: string[];
  note?: string;
}

export interface QubitStateTable {
  success?: boolean;
  states: string[];
  descriptions: Record<string, string>;
  next_action: Record<string, string>;
  evidence_sources: Record<string, EvidenceSource>;
  missing_by_state: Record<string, string[]>;
}

export interface MissingEvidence {
  key: string;
  produced_by?: string[];
  note?: string;
}

export interface QubitState {
  success?: boolean;
  name: string;
  state: string;
  state_index?: number;
  description?: string;
  reasons?: string[];
  missing?: string[];
  missing_detail?: MissingEvidence[];
  next_action?: string;
  evidence?: Record<string, unknown>;
  evidence_sources?: Record<string, unknown>;
  conflicts?: unknown[];
  plan?: {
    state?: string;
    missing?: MissingEvidence[];
    next_action?: string;
  };
}

export interface QuantumJob {
  job_id?: string;
  task_id?: string;
  run_id?: string;
  status?: string;
  created_at?: number;
  started_at?: number;
  finished_at?: number;
  error?: string;
  abandoned?: boolean;
  code?: string;
  timeout?: number;
}

export interface LegacyJob {
  id: string;
  status?: string;
  stdout?: string;
  stderr?: string;
  error?: string;
  submittedAt?: string | number;
  completedAt?: string | number;
  plotPath?: string;
  qubit?: string;
  experiment?: string;
}

export interface RunNode {
  nodeId: string;
  nodeType: string;
  status: string;
  startedAt?: string;
  completedAt?: string;
  duration?: number | null;
  input?: { config?: Record<string, unknown>; resolvedContext?: Record<string, unknown> };
  output?: {
    stdout?: string;
    stderr?: string;
    error?: string;
    metrics?: unknown;
    plotPath?: string;
  };
}

export interface WorkflowRun {
  id: string;
  workflowId: string;
  workflowName: string;
  status: string;
  startedAt?: string;
  completedAt?: string;
  totalDuration?: number;
  context?: Record<string, unknown>;
  nodes?: RunNode[];
}

export interface CanvasNode {
  id: string;
  type: string;
  position?: { x: number; y: number };
  label?: string;
  config?: Record<string, unknown>;
  dsl?: { op?: string; as?: string; stmt?: string };
  depends?: string[];
}

export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  data?: { type?: string };
}

export interface WorkflowCanvas {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

// ── 端点 ────────────────────────────────────────────────────────────────────

export const controlApi = {
  stateTable: () => getJson<QubitStateTable>("/api/quantum/qubit/state-table"),

  qubits: () => getJson<{ qubits?: string[]; count?: number; source?: string }>(
    "/api/quantum/qubits"
  ),

  qubitState: (name: string) =>
    postJson<QubitState>("/api/quantum/qubit/state", { name }),

  quantumJobs: () => getJson<{ jobs?: QuantumJob[] }>("/api/quantum/jobs"),

  legacyJobs: () => getJson<LegacyJob[]>("/jobs"),

  workflowRuns: () =>
    getJson<{ runs?: WorkflowRun[]; count?: number }>("/api/workflow-runs"),

  workflowCanvas: (id: string) =>
    getJson<{ ok?: boolean; id?: string; name?: string; canvas?: WorkflowCanvas }>(
      `/api/workflows/${encodeURIComponent(id)}`
    ),

  plotUrl: (jobId: string) => `${CONTROL_API_BASE}/plot/${jobId}`,
};

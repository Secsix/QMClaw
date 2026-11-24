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

/** 边上登记的动作（`config/state_machine.json` 的 `edges[].action`）。 */
export interface EdgeAction {
  fn?: string;
  alternatives?: string[];
  params_hint?: Record<string, string>;
}

/**
 * 状态机的一条边（三类边，设计稿 §5）：
 *   - `forward` 前向：推进状态；
 *   - `self`    自环：同态重测（重扫 / 重扫脉幅 / 重定位），有 `max_attempts` 上限；
 *   - `back`    回退：证据失效时退回去重做。
 */
export interface StateMachineEdge {
  id: string;
  kind?: "forward" | "self" | "back" | string;
  from?: string;
  to?: string;
  action?: EdgeAction;
  requires?: string[];
  max_attempts?: number;
  guard?: { evidence?: string[]; ttl_s?: number | null };
  note?: string;
  semantic?: string;
  trigger?: string;
}

/** 一道闸门（GATE-PRE / GATE-VER / GATE-TTL）的配置。 */
export interface GateConfig {
  id?: string;
  description?: string;
  on_violation?: string;
  on_version_mismatch?: string;
  recovery?: string;
  note?: string;
  report?: string[];
}

export interface QubitStateTable {
  success?: boolean;
  states: string[];
  descriptions: Record<string, string>;
  next_action: Record<string, string>;
  evidence_sources: Record<string, EvidenceSource>;
  missing_by_state: Record<string, string[]>;
  // --- 状态机结构（P0/P1 起由 state_table_extended 提供）---
  edges?: StateMachineEdge[];
  gates?: Record<string, GateConfig>;
  state_version?: string;
  state_ttl?: Record<string, number | null>;
}

/** 状态时效结论（`record_validity` 的返回）。 */
export interface StateValidity {
  valid?: boolean;
  expired?: boolean;
  expires_at?: number | null;
  reason?: string;
}

export interface MissingEvidence {
  key: string;
  produced_by?: string[];
  note?: string;
}

/**
 * `POST /qubit/state` 的实际返回形状（实测自 services/common/qubit_state.py）：
 *
 * {
 *   "success": true,
 *   "name": "q0",
 *   "state": {                       // ← 派生结果，是个**对象**，不是字符串
 *     "state": "unknown",
 *     "state_index": 0,
 *     "description": "…",
 *     "reasons": [],
 *     "missing": ["freq_locked"],
 *     "missing_detail": [{ key, produced_by, note }],
 *     "next_action": "…",
 *     "metrics": {},
 *     "criteria_version": "1.0.0"
 *   },
 *   "evidence": {},
 *   "evidence_sources": {},
 *   "conflicts": [],
 *   "records": { "considered": 0, "ignored": 27, "ignored_runs": [...], "limit": 200 },
 *   "plan": { "state": "unknown", "missing": [{ key, produced_by, note }], "next_action": "…" }
 * }
 */
export interface QubitStateDetail {
  state: string;
  state_index?: number;
  description?: string;
  reasons?: string[];
  missing?: string[];
  missing_detail?: MissingEvidence[];
  next_action?: string;
  metrics?: Record<string, unknown>;
  criteria_version?: string;
}

export interface QubitState {
  success?: boolean;
  name: string;
  state?: QubitStateDetail;
  evidence?: Record<string, unknown>;
  evidence_sources?: Record<string, unknown>;
  conflicts?: unknown[];
  records?: {
    considered?: number;
    ignored?: number;
    ignored_runs?: string[];
    limit?: number;
  };
  plan?: {
    state?: string;
    missing?: MissingEvidence[];
    next_action?: string;
  };
  // --- 以下为 P0/P1 增补（`/qubit/state` 只增不改）---
  state_version?: string;
  /** 该状态的过期时间戳（秒，epoch）；null = 永不过期（unknown）。 */
  expires_at?: number | null;
  valid?: boolean;
  validity?: StateValidity;
  /** A(s)：当前态下允许的全部转移边（前向 + 自环 + 回退）。 */
  edges?: StateMachineEdge[];
  record?: Record<string, unknown>;
  /**
   * 演练证据（manual evidence，BACKLOG O8）。
   * 演示脚本写进 data/state_ledger/evidence.json 的证据；真实台账证据优先，
   * 这里只列「补进来的那些」，前端必须标注来源，不能当成实测。
   */
  drill_evidence?: DrillEvidence;
}

/** 一条演练证据的来源信息（后端 evidence_sources 里对应的那一条）。 */
export interface DrillEvidenceEntry {
  source?: string;
  run_id?: string | null;
  note?: string;
  created_at?: number | null;
  expires_at?: number | null;
  qubit?: string;
}

export interface DrillEvidence {
  count?: number;
  keys?: string[];
  entries?: Record<string, DrillEvidenceEntry>;
}

/** `POST /qubit/evidence` 的返回（写入时带 written，读取时带 entries）。 */
export interface DrillEvidenceResponse {
  success?: boolean;
  name?: string;
  count?: number;
  entries?: Array<{
    key?: string;
    value?: unknown;
    source?: string;
    run_id?: string | null;
    note?: string;
    created_at?: number | null;
    expires_at?: number | null;
    active?: boolean;
  }>;
  written?: Array<Record<string, unknown>>;
  summary?: Record<string, unknown>;
  path?: string;
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

/**
 * 已保存的工作流条目（GET /api/workflows）。
 * 注意：`id` 才是能取到 canvas 的键；`workflowId` 是 DSL 侧的标识，
 * 而运行记录里的 workflowId 往往是一次性临时 id，两者不一定相等。
 */
export interface SavedWorkflow {
  id: string;
  workflowId?: string;
  name?: string;
  title?: string;
  nodeCount?: number;
}

// ── 端点 ────────────────────────────────────────────────────────────────────

export const controlApi = {
  stateTable: () => getJson<QubitStateTable>("/api/quantum/qubit/state-table"),

  /**
   * 后端返回的是对象数组（{ name, f10, fread, bias_z } …），
   * 这里统一收敛成比特名字数组，方便下拉框直接使用。
   */
  qubits: async () => {
    const res = await getJson<{
      qubits?: Array<string | { name?: string }>;
      count?: number;
      source?: string;
    }>("/api/quantum/qubits");
    const names = (res?.qubits ?? [])
      .map((q) => (typeof q === "string" ? q : (q?.name ?? "")))
      .filter((n) => !!n);
    return {
      qubits: names,
      count: res?.count ?? names.length,
      source: res?.source,
    };
  },

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

  /**
   * 已保存的工作流列表。
   * 运行记录里的 workflowId 常常是一次性临时 id，取不到 canvas；
   * 需要用这份列表把它映射到已保存工作流的 id（只有 id 能取到 canvas）。
   */
  workflows: () =>
    getJson<{ ok?: boolean; workflows?: SavedWorkflow[] }>("/api/workflows"),

  /**
   * 静态图 URL。Express 把 qmclaw-web/public/plots 挂在 `/plots` 下，
   * 而后端产出的 plotPath 形如 `/plots/xxx.png`（也可能只是文件名）。
   * 语义与 lib/api.ts 的 normalizePlotUrl 保持一致。
   */
  /**
   * 演练证据（manual evidence）：演示脚本写，前端只读 + 展示来源。
   * 传 name 只读/清该比特；不传则清全表（reset）。
   */
  drillEvidence: (name: string) =>
    postJson<DrillEvidenceResponse>("/api/quantum/qubit/evidence", { name }),

  setDrillEvidence: (payload: {
    name: string;
    entries: Record<string, unknown>;
    source?: string;
    ttl_s?: number;
    note?: string;
    run_id?: string;
  }) =>
    postJson<DrillEvidenceResponse>("/api/quantum/qubit/evidence", payload),

  resetDrillEvidence: (name?: string) =>
    postJson<DrillEvidenceResponse>(
      "/api/quantum/qubit/evidence/reset",
      name ? { name } : {}
    ),

  plotUrl: (path: string) => {
    if (!path) return "";
    if (path.startsWith("http") || path.startsWith("data:")) return path;
    if (path.startsWith("/")) return `${CONTROL_API_BASE}${path}`;
    return `${CONTROL_API_BASE}/plots/${path}`;
  },
};

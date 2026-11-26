"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  controlApi,
  QuantumJob,
  LegacyJob,
  QubitState,
  QubitStateTable,
  ScopeSummary,
  ScopesResponse,
  WorkflowCanvas,
  WorkflowRun,
} from "@/lib/controlApi";

/** 轮询间隔（ms）。插件页是"跟随后端"的展示面，3 秒一次足够跟手又不至于压后端。 */
const POLL_MS = 3000;

/**
 * scopes 端点拿不到时的兜底（老后端 / 服务没重启）。
 * 只留单比特一张图：多出来那几张在后端还没表态前不该凭空画。
 */
const FALLBACK_SCOPES: ScopeSummary[] = [
  { scope: "qubit", label: "单比特六态", enabled: true, reason: "scopes 端点不可用，按旧行为只显示单比特", source: "structure" },
];

export interface ControlData {
  stateTable: QubitStateTable | null;
  /** 当前芯片启用了哪几张状态图（qubit / pair / pulse / tunable），以及启用依据。 */
  scopes: ScopeSummary[];
  scopeInfo: ScopesResponse | null;
  /** 正在看哪张状态图；默认单比特六态。 */
  currentScope: string;
  setCurrentScope: (scope: string) => void;
  qubits: string[];
  selectedQubit: string;
  setSelectedQubit: (name: string) => void;
  qubitState: QubitState | null;
  quantumJobs: QuantumJob[];
  legacyJobs: LegacyJob[];
  runs: WorkflowRun[];
  activeRun: WorkflowRun | null;
  canvas: WorkflowCanvas | null;
  loading: boolean;
  error: string | null;
  /** 部分端点失败时的降级提示（不阻断其它面板）。 */
  partialErrors: string[];
  lastUpdated: number | null;
  refresh: () => void;
}

export function useControlData(): ControlData {
  const [stateTable, setStateTable] = useState<QubitStateTable | null>(null);
  const [scopes, setScopes] = useState<ScopeSummary[]>([]);
  const [scopeInfo, setScopeInfo] = useState<ScopesResponse | null>(null);
  const [currentScope, setCurrentScope] = useState<string>("qubit");
  const [qubits, setQubits] = useState<string[]>([]);
  const [selectedQubit, setSelectedQubit] = useState<string>("");
  const [qubitState, setQubitState] = useState<QubitState | null>(null);
  const [quantumJobs, setQuantumJobs] = useState<QuantumJob[]>([]);
  const [legacyJobs, setLegacyJobs] = useState<LegacyJob[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [canvas, setCanvas] = useState<WorkflowCanvas | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [partialErrors, setPartialErrors] = useState<string[]>([]);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  // 画布按 run 的 workflowId 取；记录上一个取过的 id，避免每次轮询重复请求。
  const canvasFor = useRef<{ id: string; canvas: WorkflowCanvas | null }>({
    id: "",
    canvas: null,
  });

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      // 用 allSettled：单个端点挂掉（例：后端 python 服务没重启导致 state-table 502）
      // 不应该把整页数据清空，只有"全部失败"才算致命错误。
      const results = await Promise.allSettled([
        controlApi.stateTable(currentScope),
        controlApi.qubits(),
        controlApi.quantumJobs(),
        controlApi.legacyJobs(),
        controlApi.workflowRuns(),
        controlApi.scopes(),
      ]);

      if (cancelled) return;

      const labels = [
        "state-table",
        "qubits",
        "quantum-jobs",
        "legacy-jobs",
        "workflow-runs",
        "scopes",
      ];
      const partial: string[] = [];
      results.forEach((r, i) => {
        if (r.status === "rejected") {
          partial.push(
            `${labels[i]}: ${r.reason instanceof Error ? r.reason.message : String(r.reason)}`
          );
        }
      });

      const ok = <T,>(i: number, fallback: T): T =>
        results[i].status === "fulfilled"
          ? ((results[i] as PromiseFulfilledResult<T>).value ?? fallback)
          : fallback;

      const table = ok<QubitStateTable | null>(0, null);
      const qubitList = ok<{ qubits?: string[] }>(1, { qubits: [] });
      const qJobs = ok<{ jobs?: QuantumJob[] }>(2, { jobs: [] });
      const lJobs = ok<LegacyJob[]>(3, []);
      const runList = ok<{ runs?: WorkflowRun[] }>(4, { runs: [] });
      const scopeData = ok<ScopesResponse | null>(5, null);

      setStateTable(table);
      setQubits(qubitList?.qubits ?? []);
      setQuantumJobs(qJobs?.jobs ?? []);
      setLegacyJobs(Array.isArray(lJobs) ? lJobs : []);
      setRuns(runList?.runs ?? []);
      setScopeInfo(scopeData);
      setScopes(scopeData?.scopes ?? FALLBACK_SCOPES);
      // 全挂才是致命错误；部分失败降级为提示，页面其余部分照常展示。
      setError(partial.length === results.length ? partial.join("；") : null);
      setPartialErrors(partial.length === results.length ? [] : partial);
      setLastUpdated(Date.now());
      if (!cancelled) setLoading(false);
    };

    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tick, currentScope]);

  // 没选比特时默认取第一个
  useEffect(() => {
    if (!selectedQubit && qubits.length > 0) {
      setSelectedQubit(qubits[0]);
    }
  }, [qubits, selectedQubit]);

  // 单比特状态（换了比特就重新拉）
  useEffect(() => {
    if (!selectedQubit) {
      setQubitState(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const st = await controlApi.qubitState(selectedQubit, currentScope);
        if (!cancelled) setQubitState(st);
      } catch {
        if (!cancelled) setQubitState(null);
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selectedQubit, currentScope]);

  /** 当前"活跃"的 run：优先取正在运行的，没有就取最近一次。 */
  const activeRun = useMemo<WorkflowRun | null>(() => {
    if (runs.length === 0) return null;
    const running = runs.find((r) =>
      ["running", "pending", "queued", "started"].includes(
        String(r.status || "").toLowerCase()
      )
    );
    return running ?? runs[0];
  }, [runs]);

  // 画布：跟着 activeRun 的 workflowId 走，同 id 不重复取。
  // 注意：运行记录里的 workflowId 往往是一次性临时 id，直接取 canvas 会 404；
  // 这里先用已保存工作流列表把它映射到 saved id 再取一次。
  useEffect(() => {
    const wfId = activeRun?.workflowId;
    if (!wfId) {
      setCanvas(null);
      canvasFor.current = { id: "", canvas: null };
      return;
    }
    if (canvasFor.current.id === wfId) {
      setCanvas(canvasFor.current.canvas);
      return;
    }
    let cancelled = false;
    const load = async () => {
      let found: WorkflowCanvas | null = null;

      // ① 直接按 workflowId 取
      try {
        const res = await controlApi.workflowCanvas(wfId);
        if ((res?.canvas?.nodes ?? []).length > 0) found = res.canvas ?? null;
      } catch {
        /* 下面走映射重试 */
      }

      // ② 映射到已保存工作流的 id 再取
      if (!found) {
        try {
          const list = await controlApi.workflows();
          const hit = (list?.workflows ?? []).find(
            (w) => w.id === wfId || w.workflowId === wfId
          );
          if (hit?.id) {
            const res = await controlApi.workflowCanvas(hit.id);
            if ((res?.canvas?.nodes ?? []).length > 0) found = res.canvas ?? null;
          }
        } catch {
          /* 拿不到就让面板回退到 run.nodes 链 */
        }
      }

      if (cancelled) return;
      canvasFor.current = { id: wfId, canvas: found };
      setCanvas(found);
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [activeRun?.workflowId]);

  return {
    stateTable,
    scopes,
    scopeInfo,
    currentScope,
    setCurrentScope,
    qubits,
    selectedQubit,
    setSelectedQubit,
    qubitState,
    quantumJobs,
    legacyJobs,
    runs,
    activeRun,
    canvas,
    loading,
    error,
    partialErrors,
    lastUpdated,
    refresh,
  };
}

export default useControlData;

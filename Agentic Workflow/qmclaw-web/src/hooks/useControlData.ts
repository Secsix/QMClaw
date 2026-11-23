"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  controlApi,
  QuantumJob,
  LegacyJob,
  QubitState,
  QubitStateTable,
  WorkflowCanvas,
  WorkflowRun,
} from "@/lib/controlApi";

/** 轮询间隔（ms）。插件页是"跟随后端"的展示面，3 秒一次足够跟手又不至于压后端。 */
const POLL_MS = 3000;

export interface ControlData {
  stateTable: QubitStateTable | null;
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
  lastUpdated: number | null;
  refresh: () => void;
}

export function useControlData(): ControlData {
  const [stateTable, setStateTable] = useState<QubitStateTable | null>(null);
  const [qubits, setQubits] = useState<string[]>([]);
  const [selectedQubit, setSelectedQubit] = useState<string>("");
  const [qubitState, setQubitState] = useState<QubitState | null>(null);
  const [quantumJobs, setQuantumJobs] = useState<QuantumJob[]>([]);
  const [legacyJobs, setLegacyJobs] = useState<LegacyJob[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [canvas, setCanvas] = useState<WorkflowCanvas | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
      try {
        const [table, qubitList, qJobs, lJobs, runList] = await Promise.all([
          controlApi.stateTable(),
          controlApi.qubits().catch(() => ({ qubits: [] as string[] })),
          controlApi.quantumJobs().catch(() => ({ jobs: [] as QuantumJob[] })),
          controlApi.legacyJobs().catch(() => [] as LegacyJob[]),
          controlApi.workflowRuns().catch(() => ({ runs: [] as WorkflowRun[] })),
        ]);

        if (cancelled) return;

        setStateTable(table);
        setQubits(qubitList?.qubits ?? []);
        setQuantumJobs(qJobs?.jobs ?? []);
        setLegacyJobs(Array.isArray(lJobs) ? lJobs : []);
        setRuns(runList?.runs ?? []);
        setError(null);
        setLastUpdated(Date.now());
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tick]);

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
        const st = await controlApi.qubitState(selectedQubit);
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
  }, [selectedQubit]);

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
    controlApi
      .workflowCanvas(wfId)
      .then((res) => {
        if (cancelled) return;
        const c = res?.canvas ?? null;
        canvasFor.current = { id: wfId, canvas: c };
        setCanvas(c);
      })
      .catch(() => {
        if (cancelled) return;
        canvasFor.current = { id: wfId, canvas: null };
        setCanvas(null);
      });
    return () => {
      cancelled = true;
    };
  }, [activeRun?.workflowId]);

  return {
    stateTable,
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
    lastUpdated,
    refresh,
  };
}

export default useControlData;

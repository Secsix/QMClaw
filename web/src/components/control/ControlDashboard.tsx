"use client";

import { useCallback, useMemo } from "react";

import ControlCardGrid, { type ControlCardContent } from "@/components/control/ControlCardGrid";
import ControlHeader from "@/components/control/ControlHeader";
import LiveExperimentPanel from "@/components/control/LiveExperimentPanel";
import QubitStateGraph from "@/components/control/QubitStateGraph";
import QwfFlowPanel from "@/components/control/QwfFlowPanel";
import { useControlLayout } from "@/components/control/useControlLayout";
import useControlData from "@/hooks/useControlData";

/**
 * /control 插件页：可自由布局的四张卡片。
 *   1) 测控状态转移图 —— 当前比特处于什么测控状态、转移到下一状态缺什么实验
 *   2) QWF 流程图     —— 当前调用的测控流程图、节点、运行了哪些实验
 *   3) 实验动态       —— 当前正在跑什么 + 上一个刚跑完的实验及结果
 *   4) 运行记录       —— 工作流历史
 *
 * 位置/大小/显隐由用户自己拖（默认只读，点「编辑布局」进入编辑态），
 * 按断点分别记在 localStorage：见 controlLayout.ts / useControlLayout.ts。
 */

export default function ControlDashboard() {
  const data = useControlData();
  const layout = useControlLayout();

  const runStatus = useMemo(() => {
    const s = String(data.activeRun?.status ?? "").toLowerCase();
    if (!data.activeRun) return "idle";
    if (s.includes("run") || s.includes("pending") || s.includes("queued")) return "running";
    return s || "idle";
  }, [data.activeRun]);

  const handleSelect = useCallback(
    (name: string) => data.setSelectedQubit(name),
    [data]
  );

  const cards: ControlCardContent[] = [
    {
      id: "stateGraph",
      subtitle: data.selectedQubit ? `比特 ${data.selectedQubit}` : "未选择比特",
      body: (
        <QubitStateGraph
          table={data.stateTable}
          state={data.qubitState}
          qubitName={data.selectedQubit || "—"}
        />
      ),
    },
    {
      id: "qwfFlow",
      subtitle: data.activeRun
        ? `${data.activeRun.workflowName || data.activeRun.workflowId} · ${data.activeRun.status}`
        : "暂无运行记录",
      right:
        data.activeRun?.totalDuration != null ? (
          <span className="qm-mono-note">{data.activeRun.totalDuration} ms</span>
        ) : null,
      body: <QwfFlowPanel canvas={data.canvas} run={data.activeRun} height="100%" />,
    },
    {
      id: "liveExperiment",
      subtitle: "节点图 · 运行中 · 近期作业",
      body: (
        <LiveExperimentPanel
          quantumJobs={data.quantumJobs}
          legacyJobs={data.legacyJobs}
          run={data.activeRun}
        />
      ),
    },
    {
      id: "runs",
      subtitle: "工作流历史 · 最近 8 条",
      body: (
        <div className="qm-run-list">
          {data.runs.slice(0, 8).map((r) => {
            const st = String(r.status).toLowerCase();
            const tone = st.includes("run")
              ? "run"
              : st.includes("fail") || st.includes("error")
                ? "fail"
                : "ok";
            return (
              <div key={r.id} className="qm-run-row">
                <span className="qm-run-status" data-tone={tone}>
                  {r.status}
                </span>
                <span className="qm-run-name">
                  {r.workflowName || r.workflowId}
                </span>
                <span className="qm-run-meta">{(r.nodes ?? []).length} 节点</span>
                <span className="qm-run-time">
                  {r.startedAt ? String(r.startedAt).slice(11, 19) : ""}
                </span>
              </div>
            );
          })}
          {data.runs.length === 0 ? (
            <span className="qm-empty-inline">暂无运行记录</span>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <div className="qm-control-root">
      <ControlHeader
        qubits={data.qubits}
        selectedQubit={data.selectedQubit}
        onSelectQubit={handleSelect}
        onRefresh={data.refresh}
        status={runStatus}
        lastUpdated={data.lastUpdated}
        error={data.error}
        partialErrors={data.partialErrors}
      />

      <main className="qm-control-main">
        <ControlCardGrid layout={layout} cards={cards} />
      </main>
    </div>
  );
}

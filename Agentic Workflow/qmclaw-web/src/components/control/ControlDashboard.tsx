"use client";

import { useCallback, useMemo } from "react";

import ControlHeader from "@/components/control/ControlHeader";
import LiveExperimentPanel from "@/components/control/LiveExperimentPanel";
import QubitStateGraph from "@/components/control/QubitStateGraph";
import QwfFlowPanel from "@/components/control/QwfFlowPanel";
import useControlData from "@/hooks/useControlData";

/**
 * /control 插件页：三图布局。
 *   1) 测控状态转移图 —— 当前比特处于什么测控状态、转移到下一状态缺什么实验
 *   2) QWF 流程图     —— 当前调用的测控流程图、节点、运行了哪些实验
 *   3) 实验动态       —— 当前正在跑什么 + 上一个刚跑完的实验及结果
 */

function Section({
  title,
  subtitle,
  right,
  children,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      style={{
        background: "var(--qm-bg)",
        border: "1px solid var(--qm-border)",
        borderRadius: "0.6rem",
        padding: "0.7rem 0.8rem",
        display: "flex",
        flexDirection: "column",
        gap: "0.55rem",
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "0.5rem",
          flexWrap: "wrap",
        }}
      >
        <h2 style={{ margin: 0, fontSize: "0.9rem", color: "var(--qm-text)" }}>
          {title}
        </h2>
        {subtitle ? (
          <span style={{ fontSize: "0.72rem", color: "var(--qm-text-mute)" }}>
            {subtitle}
          </span>
        ) : null}
        {right ? <span style={{ marginLeft: "auto" }}>{right}</span> : null}
      </div>
      {children}
    </section>
  );
}

export default function ControlDashboard() {
  const data = useControlData();

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

  return (
    <div
      style={{
        minHeight: "100dvh",
        background: "var(--qm-bg)",
        color: "var(--qm-text)",
        fontFamily: "system-ui, sans-serif",
        display: "flex",
        flexDirection: "column",
      }}
    >
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

      <main
        style={{
          flex: 1,
          overflow: "auto",
          padding: "0.8rem",
          display: "grid",
          gap: "0.8rem",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          alignContent: "start",
        }}
      >
        <Section
          title="① 测控状态转移图"
          subtitle={
            data.selectedQubit
              ? `比特 ${data.selectedQubit}`
              : "未选择比特"
          }
        >
          <QubitStateGraph
            table={data.stateTable}
            state={data.qubitState}
            qubitName={data.selectedQubit || "—"}
          />
        </Section>

        <Section
          title="② QWF 流程图"
          subtitle={
            data.activeRun
              ? `${data.activeRun.workflowName || data.activeRun.workflowId} · ${data.activeRun.status}`
              : "暂无运行记录"
          }
          right={
            data.activeRun?.totalDuration != null ? (
              <span style={{ fontSize: "0.7rem", color: "var(--qm-text-mute)" }}>
                总耗时 {data.activeRun.totalDuration}ms
              </span>
            ) : null
          }
        >
          <QwfFlowPanel canvas={data.canvas} run={data.activeRun} />
        </Section>

        <Section
          title="③ 实验动态"
          subtitle="本次运行的每个节点图 / 当前运行中 / 上一个已结束 / 近期作业"
        >
          <LiveExperimentPanel
            quantumJobs={data.quantumJobs}
            legacyJobs={data.legacyJobs}
            run={data.activeRun}
          />
        </Section>

        <Section
          title="运行记录"
          subtitle="工作流历史（最近 8 条）"
        >
          <div style={{ display: "grid", gap: "0.25rem" }}>
            {data.runs.slice(0, 8).map((r) => (
              <div
                key={r.id}
                style={{
                  display: "flex",
                  gap: "0.5rem",
                  alignItems: "baseline",
                  fontSize: "0.75rem",
                  color: "var(--qm-text-dim)",
                  flexWrap: "wrap",
                }}
              >
                <span
                  style={{
                    color:
                      String(r.status).toLowerCase().includes("run")
                        ? "var(--qm-accent)"
                        : String(r.status).toLowerCase().includes("fail")
                          ? "var(--qm-err)"
                          : "var(--qm-ok)",
                  }}
                >
                  {r.status}
                </span>
                <span style={{ color: "var(--qm-text)" }}>
                  {r.workflowName || r.workflowId}
                </span>
                <span style={{ color: "var(--qm-text-mute)" }}>
                  {(r.nodes ?? []).length} 节点
                </span>
                <span style={{ marginLeft: "auto", color: "var(--qm-text-mute)" }}>
                  {r.startedAt ? String(r.startedAt).slice(11, 19) : ""}
                </span>
              </div>
            ))}
            {data.runs.length === 0 ? (
              <span style={{ fontSize: "0.75rem", color: "var(--qm-text-mute)" }}>
                暂无运行记录
              </span>
            ) : null}
          </div>
        </Section>
      </main>
    </div>
  );
}

"use client";

import { useCallback, useMemo } from "react";

import ControlCardGrid, { type ControlCardContent } from "@/components/control/ControlCardGrid";
import ControlHeader from "@/components/control/ControlHeader";
import LiveExperimentPanel from "@/components/control/LiveExperimentPanel";
import QubitStateGraph from "@/components/control/QubitStateGraph";
import QwfFlowPanel from "@/components/control/QwfFlowPanel";
import { useControlLayout } from "@/components/control/useControlLayout";
import useControlData from "@/hooks/useControlData";
import type { ScopeSummary } from "@/lib/controlApi";

/**
 * /control 插件页：可自由布局的四张卡片。
 *   1) 测控状态转移图 —— 当前比特处于什么测控状态、转移到下一状态缺什么实验
 *      图里有哪几张（单比特六态 / 双比特 CZ / 脉冲质量 / 频率空间）由后端
 *      `GET /api/quantum/qubit/scopes` 决定：状态图随芯片调控难点变化，
 *      前端只画启用的那几张，并在卡片上写明为什么启用。
 *   2) QWF 流程图     —— 当前调用的测控流程图、节点、运行了哪些实验
 *   3) 实验动态       —— 当前正在跑什么 + 上一个刚跑完的实验及结果
 *   4) 运行记录       —— 工作流历史
 *
 * 位置/大小/显隐由用户自己拖（默认只读，点「编辑布局」进入编辑态），
 * 按断点分别记在 localStorage：见 controlLayout.ts / useControlLayout.ts。
 */

/** 这张图为什么被启用（一句话，给卡片副标题用）。 */
function scopeBasis(info: ScopeSummary | undefined): string {
  if (!info) return "";
  if (info.source === "override") return "人工启用";
  if (info.enabled_by && info.enabled_by.length > 0) {
    return info.source === "capabilities"
      ? `按能力启用：${info.enabled_by.join(" / ")}`
      : "";
  }
  return info.source === "structure" ? "结构表默认" : "";
}

/** 鼠标悬停时看完整依据。 */
function scopeTitle(info: ScopeSummary): string {
  const parts = [info.label ?? info.scope];
  if (info.prerequisite?.state) {
    parts.push(
      `前置：${info.prerequisite.scope ?? "qubit"} 到 ${info.prerequisite.state}`
    );
  }
  if (info.engine) parts.push(`判据引擎：${info.engine}`);
  if (info.enabled_by?.length) parts.push(`能力标签：${info.enabled_by.join(", ")}`);
  parts.push(info.enabled ? `已启用（${info.reason ?? "结构表"}）` : `未启用（${info.reason ?? "缺能力"}）`);
  return parts.join("\n");
}

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

  const enabledScopes = useMemo(
    () => data.scopes.filter((s) => s.enabled),
    [data.scopes]
  );
  const hiddenScopes = useMemo(
    () => data.scopes.filter((s) => !s.enabled),
    [data.scopes]
  );

  // 副标题：看的是哪张图、依据是什么、当前比特是谁
  const scopeSubtitle = useMemo(() => {
    const info = data.scopes.find((s) => s.scope === data.currentScope);
    const label = info?.label ?? data.currentScope;
    const basis = scopeBasis(info);
    const qubit = data.selectedQubit ? `比特 ${data.selectedQubit}` : "未选择比特";
    return [label, basis, qubit].filter(Boolean).join(" · ");
  }, [data.scopes, data.currentScope, data.selectedQubit]);

  const cards: ControlCardContent[] = [
    {
      id: "stateGraph",
      subtitle: scopeSubtitle,
      right: (
        <div className="qm-scope-switch">
          {enabledScopes.map((info) => (
            <button
              key={info.scope}
              type="button"
              className="qm-btn"
              data-active={info.scope === data.currentScope}
              title={scopeTitle(info)}
              onClick={() => data.setCurrentScope(info.scope)}
            >
              {info.label ?? info.scope}
            </button>
          ))}
          {hiddenScopes.length > 0 ? (
            <span
              className="qm-layout-hint"
              title={hiddenScopes.map((s) => `${s.label ?? s.scope}：${s.reason ?? "未启用"}`).join("\n")}
            >
              未启用 {hiddenScopes.length}
            </span>
          ) : null}
        </div>
      ),
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

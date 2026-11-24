"use client";

import { useEffect, useMemo, useState } from "react";

import { LegacyJob, QuantumJob, WorkflowRun } from "@/lib/controlApi";
import { controlApi } from "@/lib/controlApi";

/**
 * 第三个图：当前正在跑什么实验 + 上一个刚跑完的实验及其结果。
 *
 * 两条通道合并：
 *   - 新通道 GET /api/quantum/jobs（micro service 作业台账，含 job_id/status/时间戳）
 *   - 老通道 GET /jobs（老队列，含 stdout/stderr/plotPath/qubit/experiment 解析结果）
 *
 * 老通道的信息更全（能显示实验名、比特、stdout、图片），所以：
 *   "上一个跑完"优先从老通道里挑最新的已完成作业；老通道没有才退回新通道。
 *
 * 第三块「本次运行图」（BACKLOG O8）：工作流每个节点自己产出的图
 * （run.nodes[].output.plotPath），按节点顺序铺成缩略图，点开看大图。
 * 这一块和老队列那张图不是一回事：老队列只有"最后一张"，这里能看到
 * 一次 QWF 里每一步分别画了什么 —— 演示时"状态推进 → 每步都出图"靠它。
 */

interface Props {
  quantumJobs: QuantumJob[];
  legacyJobs: LegacyJob[];
  /** 当前这次工作流运行（含逐节点 output.plotPath）。 */
  run?: WorkflowRun | null;
}

interface NodePlot {
  nodeId: string;
  nodeType: string;
  status: string;
  duration?: number | null;
  path: string;
  src: string;
}

const RUNNING = ["running", "started", "pending", "queued", "submitted"];

function ts(v?: string | number): string {
  if (v === undefined || v === null || v === "") return "";
  const n = typeof v === "number" ? (v > 1e12 ? v : v * 1000) : Date.parse(v);
  if (!Number.isFinite(n)) return String(v);
  const d = new Date(n);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function duration(from?: string | number, to?: string | number): string {
  if (!from || !to) return "";
  const a = typeof from === "number" ? from : Date.parse(from);
  const b = typeof to === "number" ? to : Date.parse(to);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return "";
  const s = (b - a) / 1000;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m${Math.round(s % 60)}s`;
}

function orderJobs(jobs: LegacyJob[]): LegacyJob[] {
  return [...jobs].sort((a, b) => {
    const ta = Date.parse(String(a.completedAt ?? a.submittedAt ?? "")) || 0;
    const tb = Date.parse(String(b.completedAt ?? b.submittedAt ?? "")) || 0;
    return tb - ta;
  });
}

function orderQuantum(jobs: QuantumJob[]): QuantumJob[] {
  return [...jobs].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
}

function Badge({ status }: { status?: string }) {
  const s = (status || "unknown").toLowerCase();
  const color =
    s.includes("fail") || s.includes("error")
      ? "var(--qm-err)"
      : s.includes("run")
        ? "var(--qm-accent)"
        : s.includes("complete") || s.includes("success") || s.includes("done")
          ? "var(--qm-ok)"
          : "var(--qm-text-mute)";
  return (
    <span
      style={{
        color,
        border: `1px solid ${color}`,
        borderRadius: "0.25rem",
        padding: "0.02rem 0.32rem",
        fontSize: "0.68rem",
        whiteSpace: "nowrap",
      }}
    >
      {status || "unknown"}
    </span>
  );
}

function JobCard({
  title,
  badge,
  meta,
  body,
  accent,
}: {
  title: string;
  badge?: string;
  meta: string[];
  body?: React.ReactNode;
  accent: string;
}) {
  return (
    <div
      style={{
        border: `1px solid ${accent}`,
        borderRadius: "0.5rem",
        background: "var(--qm-panel)",
        padding: "0.6rem 0.7rem",
        display: "flex",
        flexDirection: "column",
        gap: "0.35rem",
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: "0.78rem", color: accent, fontWeight: 700 }}>
          {title}
        </span>
        <Badge status={badge} />
      </div>
      {meta.length > 0 && (
        <div
          style={{
            display: "flex",
            gap: "0.6rem",
            flexWrap: "wrap",
            fontSize: "0.72rem",
            color: "var(--qm-text-dim)",
          }}
        >
          {meta.map((m, i) => (
            <span key={i}>{m}</span>
          ))}
        </div>
      )}
      {body}
    </div>
  );
}

export default function LiveExperimentPanel({
  quantumJobs,
  legacyJobs,
  run,
}: Props) {
  const runningLegacy = useMemo(
    () => orderJobs(legacyJobs).filter((j) => RUNNING.includes((j.status || "").toLowerCase())),
    [legacyJobs]
  );
  const runningQuantum = useMemo(
    () => orderQuantum(quantumJobs).filter((j) => RUNNING.includes((j.status || "").toLowerCase())),
    [quantumJobs]
  );
  const lastLegacy = useMemo(
    () =>
      orderJobs(legacyJobs).find(
        (j) => !RUNNING.includes((j.status || "").toLowerCase())
      ),
    [legacyJobs]
  );
  const lastQuantum = useMemo(
    () =>
      orderQuantum(quantumJobs).find(
        (j) => !RUNNING.includes((j.status || "").toLowerCase())
      ),
    [quantumJobs]
  );

  const running = runningLegacy[0];

  // 本次运行里所有产出图像的节点（保持节点顺序，便于对照 QWF 流程）
  const nodePlots = useMemo<NodePlot[]>(() => {
    const out: NodePlot[] = [];
    for (const node of run?.nodes ?? []) {
      const path = node.output?.plotPath;
      if (!path) continue;
      out.push({
        nodeId: node.nodeId,
        nodeType: node.nodeType,
        status: node.status,
        duration: node.duration,
        path,
        src: controlApi.plotUrl(path),
      });
    }
    return out;
  }, [run]);

  // 点击放大：Esc 关闭（面板在窄侧栏里，键盘退出比找按钮快）
  const [zoom, setZoom] = useState<NodePlot | null>(null);
  useEffect(() => {
    if (!zoom) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoom(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoom]);

  // 「上一个已结束」优先取老队列（信息更全：实验名/比特/stdout/图），没有再退回新通道。
  const plotSrc = lastLegacy?.plotPath
    ? lastLegacy.plotPath.startsWith("http")
      ? lastLegacy.plotPath
      : controlApi.plotUrl(lastLegacy.plotPath)
    : null;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
        gap: "0.6rem",
      }}
    >
      {/* 本次运行逐节点图（BACKLOG O8） */}
      <JobCard
        title="本次运行图"
        badge={run ? `${nodePlots.length} 张` : "none"}
        accent="var(--qm-accent-2, var(--qm-accent))"
        meta={
          run
            ? [
                run.workflowName || run.workflowId || run.id,
                run.status ? `状态 ${run.status}` : "",
                run.totalDuration != null ? `总耗时 ${run.totalDuration}ms` : "",
              ].filter(Boolean)
            : ["还没有工作流运行记录"]
        }
        body={
          nodePlots.length > 0 ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
                gap: "0.4rem",
              }}
            >
              {nodePlots.map((plot) => (
                <button
                  key={`${plot.nodeId}-${plot.path}`}
                  type="button"
                  onClick={() => setZoom(plot)}
                  title={`${plot.nodeId} · ${plot.nodeType} · ${plot.status}\n${plot.path}\n（点击放大）`}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.2rem",
                    padding: "0.25rem",
                    border: "1px solid var(--qm-border)",
                    borderRadius: "0.35rem",
                    background: "var(--qm-bg-2)",
                    cursor: "zoom-in",
                    minWidth: 0,
                    textAlign: "left",
                  }}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={plot.src}
                    alt={`${plot.nodeId} 实验结果图`}
                    style={{
                      width: "100%",
                      borderRadius: "0.25rem",
                      background: "#fff",
                      display: "block",
                    }}
                  />
                  <span
                    style={{
                      fontSize: "0.68rem",
                      color: "var(--qm-text-dim)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {plot.nodeId} · {plot.nodeType}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <span style={{ fontSize: "0.72rem", color: "var(--qm-text-mute)" }}>
              本次运行没有产出图像（模拟后端可能只给指标；节点 output.plotPath 为空）
            </span>
          )
        }
      />

      {/* 当前正在运行 */}
      <JobCard
        title="当前运行中"
        badge={
          running?.status ??
          runningQuantum[0]?.status ??
          (running || runningQuantum[0] ? "running" : "idle")
        }
        accent="var(--qm-accent)"
        meta={
          running
            ? [
                running.experiment ? `实验 ${running.experiment}` : "",
                running.qubit ? `比特 ${running.qubit}` : "",
                ts(running.submittedAt)
                  ? `开始 ${ts(running.submittedAt)}`
                  : "",
              ].filter(Boolean)
            : runningQuantum[0]
              ? [
                  `job ${runningQuantum[0].job_id ?? runningQuantum[0].task_id ?? "?"}`,
                  ts(runningQuantum[0].started_at),
                ].filter(Boolean)
              : ["没有正在运行的实验"]
        }
        body={
          running?.stdout ? (
            <pre
              style={{
                margin: 0,
                maxHeight: 110,
                overflow: "auto",
                fontSize: "0.68rem",
                color: "var(--qm-text-dim)",
                background: "var(--qm-bg-2)",
                borderRadius: "0.3rem",
                padding: "0.4rem",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {running.stdout}
            </pre>
          ) : runningQuantum[0]?.code ? (
            <pre
              style={{
                margin: 0,
                maxHeight: 110,
                overflow: "auto",
                fontSize: "0.68rem",
                color: "var(--qm-text-dim)",
                background: "var(--qm-bg-2)",
                borderRadius: "0.3rem",
                padding: "0.4rem",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {runningQuantum[0].code}
            </pre>
          ) : undefined
        }
      />

      {/* 上一个刚跑完 */}
      <JobCard
        title="上一个已结束"
        badge={lastLegacy?.status ?? lastQuantum?.status ?? "none"}
        accent="var(--qm-ok)"
        meta={
          lastLegacy
            ? [
                lastLegacy.experiment ? `实验 ${lastLegacy.experiment}` : "",
                lastLegacy.qubit ? `比特 ${lastLegacy.qubit}` : "",
                duration(lastLegacy.submittedAt, lastLegacy.completedAt),
                ts(lastLegacy.completedAt),
              ].filter(Boolean)
            : lastQuantum
              ? [
                  `job ${lastQuantum.job_id ?? lastQuantum.task_id ?? "?"}`,
                  duration(lastQuantum.started_at, lastQuantum.finished_at),
                  ts(lastQuantum.finished_at),
                ].filter(Boolean)
              : ["暂无历史实验"]
        }
        body={
          <>
            {lastLegacy?.stdout ? (
              <pre
                style={{
                  margin: 0,
                  maxHeight: 120,
                  overflow: "auto",
                  fontSize: "0.68rem",
                  color: "var(--qm-text-dim)",
                  background: "var(--qm-bg-2)",
                  borderRadius: "0.3rem",
                  padding: "0.4rem",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {lastLegacy.stdout}
              </pre>
            ) : null}
            {lastLegacy?.error ? (
              <div
                style={{
                  fontSize: "0.7rem",
                  color: "var(--qm-err)",
                  background: "var(--qm-err-soft)",
                  borderRadius: "0.3rem",
                  padding: "0.3rem 0.4rem",
                }}
              >
                {lastLegacy.error}
              </div>
            ) : null}
            {lastQuantum?.error ? (
              <div
                style={{
                  fontSize: "0.7rem",
                  color: "var(--qm-err)",
                  background: "var(--qm-err-soft)",
                  borderRadius: "0.3rem",
                  padding: "0.3rem 0.4rem",
                }}
              >
                {lastQuantum.error}
              </div>
            ) : null}
            {plotSrc ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={plotSrc}
                alt="上一次实验结果图"
                style={{
                  width: "100%",
                  borderRadius: "0.35rem",
                  border: "1px solid var(--qm-border)",
                  background: "#fff",
                }}
              />
            ) : null}
          </>
        }
      />

      {/* 运行历史（最近几条，便于对照） */}
      <JobCard
        title="近期作业"
        accent="var(--qm-text-dim)"
        meta={[]}
        body={
          <div style={{ display: "grid", gap: "0.2rem", maxHeight: 160, overflow: "auto" }}>
            {orderQuantum(quantumJobs).slice(0, 8).map((j) => (
              <div
                key={j.job_id ?? j.task_id ?? String(j.created_at)}
                style={{
                  display: "flex",
                  gap: "0.4rem",
                  alignItems: "center",
                  fontSize: "0.7rem",
                  color: "var(--qm-text-dim)",
                }}
              >
                <Badge status={j.status} />
                <span style={{ color: "var(--qm-text-mute)" }}>
                  {j.job_id ?? j.task_id ?? "?"}
                </span>
                <span style={{ marginLeft: "auto" }}>{ts(j.created_at)}</span>
              </div>
            ))}
            {quantumJobs.length === 0 && legacyJobs.length === 0 ? (
              <span style={{ fontSize: "0.72rem", color: "var(--qm-text-mute)" }}>
                暂无作业记录
              </span>
            ) : null}
          </div>
        }
      />

      {zoom ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="实验结果图放大"
          onClick={() => setZoom(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 60,
            background: "rgba(2, 6, 23, 0.82)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: "0.5rem",
            padding: "1rem",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.6rem",
              color: "#e2e8f0",
              fontSize: "0.78rem",
              maxWidth: "100%",
            }}
          >
            <strong>{zoom.nodeId}</strong>
            <span style={{ opacity: 0.75 }}>
              {zoom.nodeType} · {zoom.status}
              {zoom.duration != null ? ` · ${zoom.duration}ms` : ""}
            </span>
            <span style={{ opacity: 0.6, overflow: "hidden", textOverflow: "ellipsis" }}>
              {zoom.path}
            </span>
            <button
              type="button"
              onClick={() => setZoom(null)}
              style={{
                marginLeft: "auto",
                background: "transparent",
                color: "#e2e8f0",
                border: "1px solid rgba(226,232,240,0.4)",
                borderRadius: "0.3rem",
                padding: "0.1rem 0.5rem",
                cursor: "pointer",
                fontSize: "0.75rem",
              }}
            >
              关闭（Esc）
            </button>
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={zoom.src}
            alt={`${zoom.nodeId} 实验结果图（放大）`}
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: "100%",
              maxHeight: "82vh",
              borderRadius: "0.4rem",
              background: "#fff",
              boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

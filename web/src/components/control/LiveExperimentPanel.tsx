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
  const tone =
    s.includes("fail") || s.includes("error")
      ? "fail"
      : s.includes("run")
        ? "run"
        : s.includes("complete") || s.includes("success") || s.includes("done")
          ? "ok"
          : "idle";
  return (
    <span className="qm-badge" data-tone={tone}>
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
    <div className="qm-job-card" style={{ borderLeftColor: accent }}>
      <div className="qm-job-head">
        <span className="qm-job-title" style={{ color: accent }}>
          {title}
        </span>
        <Badge status={badge} />
      </div>
      {meta.length > 0 && (
        <div className="qm-job-meta">
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
    <div className="qm-live-grid">
      {/* 本次运行逐节点图（BACKLOG O8）——整条运行的主视觉，占满整行 */}
      <div className="qm-span-all">
        <JobCard
          title="本次运行图"
          badge={run ? `${nodePlots.length} 张` : "none"}
          accent="var(--qm-accent)"
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
              <div className="qm-plot-grid" data-single={nodePlots.length === 1 ? "true" : "false"}>
                {nodePlots.map((plot) => (
                  <button
                    key={`${plot.nodeId}-${plot.path}`}
                    type="button"
                    className="qm-plot-cell"
                    onClick={() => setZoom(plot)}
                    title={`${plot.nodeId} · ${plot.nodeType} · ${plot.status}\n${plot.path}\n（点击放大）`}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={plot.src}
                      alt={`${plot.nodeId} 实验结果图`}
                      className="qm-plot-img"
                      style={{
                        maxHeight: nodePlots.length === 1 ? 560 : 340,
                      }}
                    />
                    <span className="qm-plot-cap">
                      {plot.nodeId} · {plot.nodeType} · {plot.status}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <span className="qm-empty-inline">
                本次运行没有产出图像（模拟后端可能只给指标；节点 output.plotPath 为空）
              </span>
            )
          }
        />
      </div>

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
            <pre className="qm-pre">{running.stdout}</pre>
          ) : runningQuantum[0]?.code ? (
            <pre className="qm-pre">{runningQuantum[0].code}</pre>
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
            {lastLegacy?.stdout ? <pre className="qm-pre">{lastLegacy.stdout}</pre> : null}
            {lastLegacy?.error ? <div className="qm-err-box">{lastLegacy.error}</div> : null}
            {lastQuantum?.error ? <div className="qm-err-box">{lastQuantum.error}</div> : null}
            {plotSrc ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={plotSrc} alt="上一次实验结果图" className="qm-shot" />
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
          <div className="qm-job-list">
            {orderQuantum(quantumJobs).slice(0, 8).map((j) => (
              <div key={j.job_id ?? j.task_id ?? String(j.created_at)} className="qm-job-row">
                <Badge status={j.status} />
                <span className="qm-job-id">
                  {j.job_id ?? j.task_id ?? "?"}
                </span>
                <span className="qm-job-time">{ts(j.created_at)}</span>
              </div>
            ))}
            {quantumJobs.length === 0 && legacyJobs.length === 0 ? (
              <span className="qm-empty-inline">暂无作业记录</span>
            ) : null}
          </div>
        }
      />

      {zoom ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="实验结果图放大"
          className="qm-zoom"
          onClick={() => setZoom(null)}
        >
          <div className="qm-zoom-bar">
            <strong>{zoom.nodeId}</strong>
            <span className="qm-zoom-sub">
              {zoom.nodeType} · {zoom.status}
              {zoom.duration != null ? ` · ${zoom.duration}ms` : ""}
            </span>
            <span className="qm-zoom-path">{zoom.path}</span>
            <button type="button" onClick={() => setZoom(null)} className="qm-zoom-close">
              关闭（Esc）
            </button>
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={zoom.src}
            alt={`${zoom.nodeId} 实验结果图（放大）`}
            onClick={(e) => e.stopPropagation()}
            className="qm-zoom-img"
          />
        </div>
      ) : null}
    </div>
  );
}

"use client";

import { useMemo } from "react";

import { LegacyJob, QuantumJob } from "@/lib/controlApi";
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
 */

interface Props {
  quantumJobs: QuantumJob[];
  legacyJobs: LegacyJob[];
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

export default function LiveExperimentPanel({ quantumJobs, legacyJobs }: Props) {
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
    </div>
  );
}

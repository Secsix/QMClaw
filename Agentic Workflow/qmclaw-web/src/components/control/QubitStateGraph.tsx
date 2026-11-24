"use client";

import { useMemo } from "react";

import { QubitState, QubitStateTable } from "@/lib/controlApi";

/**
 * 测控状态转移图。
 *
 * 口径全部来自后端 `services/common/qubit_state.py`（前端不硬编码状态机）：
 *   - 状态序列 / 说明 / 下一步 / 每类证据由哪个实验产出 → GET /api/quantum/qubit/state-table
 *   - 当前比特实际处于哪一态、还缺哪些证据             → POST /api/quantum/qubit/state
 *
 * 图的形状：一条单向链（unknown → … → characterized），当前态高亮，
 * 当前态之后的状态画成"未达到"（虚线框 + 降透明度），缺什么证据直接标在当前态下方。
 */

interface Props {
  table: QubitStateTable | null;
  state: QubitState | null;
  qubitName: string;
}

const NODE_W = 132;
const NODE_H = 54;
const GAP = 26;

export default function QubitStateGraph({ table, state, qubitName }: Props) {
  const states = table?.states ?? [];
  const descriptions = table?.descriptions ?? {};
  const nextActions = table?.next_action ?? {};
  const missingByState = table?.missing_by_state ?? {};
  const evidenceSources = table?.evidence_sources ?? {};

  // 后端把派生结果放在 `state` 对象里（见 controlApi.ts 的 QubitState 注释）
  const detail = state?.state ?? null;
  const current = detail?.state ?? "";
  const currentIndex = useMemo(
    () => states.indexOf(current),
    [states, current]
  );

  const missing = state?.plan?.missing ?? detail?.missing_detail ?? [];
  const nextAction = state?.plan?.next_action ?? detail?.next_action ?? "";
  const currentDescription = detail?.description ?? descriptions[current] ?? "";

  if (states.length === 0) {
    return (
      <div
        style={{
          padding: "1rem",
          color: "var(--qm-text-dim)",
          fontSize: "0.85rem",
          background: "var(--qm-panel)",
          border: "1px solid var(--qm-border)",
          borderRadius: "0.5rem",
        }}
      >
        状态机表尚未加载（后端 /api/quantum/qubit/state-table 未响应）
      </div>
    );
  }

  const width = states.length * NODE_W + (states.length - 1) * GAP;
  const height = NODE_H + 96;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
      <div style={{ overflowX: "auto", paddingBottom: "0.25rem" }}>
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="测控状态转移图"
          style={{ minWidth: width, display: "block" }}
        >
          <defs>
            <marker
              id="qm-state-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--qm-border-strong)" />
            </marker>
            <marker
              id="qm-state-arrow-active"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--qm-accent)" />
            </marker>
          </defs>

          {/* 连线 */}
          {states.slice(0, -1).map((_, i) => {
            const reached = currentIndex > i; // 已走过的边
            const x1 = i * (NODE_W + GAP) + NODE_W;
            const x2 = (i + 1) * (NODE_W + GAP);
            const y = NODE_H / 2;
            return (
              <line
                key={`edge-${i}`}
                x1={x1}
                y1={y}
                x2={x2 - 4}
                y2={y}
                stroke={
                  reached ? "var(--qm-accent)" : "var(--qm-border-strong)"
                }
                strokeWidth={reached ? 2.4 : 1.4}
                strokeDasharray={reached ? undefined : "4 4"}
                markerEnd={
                  reached
                    ? "url(#qm-state-arrow-active)"
                    : "url(#qm-state-arrow)"
                }
              />
            );
          })}

          {/* 状态节点 */}
          {states.map((s, i) => {
            const x = i * (NODE_W + GAP);
            const isCurrent = s === current;
            const reached = currentIndex >= 0 && i < currentIndex;
            const fill = isCurrent
              ? "var(--qm-accent-soft)"
              : reached
                ? "var(--qm-panel-2)"
                : "var(--qm-panel)";
            const stroke = isCurrent
              ? "var(--qm-accent)"
              : reached
                ? "var(--qm-border-strong)"
                : "var(--qm-border)";
            const opacity = currentIndex >= 0 && i > currentIndex ? 0.45 : 1;
            return (
              <g key={s} opacity={opacity}>
                <rect
                  x={x}
                  y={0}
                  width={NODE_W}
                  height={NODE_H}
                  rx={8}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={isCurrent ? 2 : 1}
                  strokeDasharray={reached || isCurrent ? undefined : "4 3"}
                />
                <text
                  x={x + NODE_W / 2}
                  y={NODE_H / 2 - 4}
                  textAnchor="middle"
                  fontSize={isCurrent ? 12 : 11}
                  fontWeight={isCurrent ? 700 : 500}
                  fill={isCurrent ? "var(--qm-accent)" : "var(--qm-text)"}
                >
                  {s}
                </text>
                <text
                  x={x + NODE_W / 2}
                  y={NODE_H / 2 + 13}
                  textAnchor="middle"
                  fontSize={9}
                  fill="var(--qm-text-mute)"
                >
                  {isCurrent ? "当前状态" : reached ? "已达成" : "未达到"}
                </text>
              </g>
            );
          })}

          {/* 当前态的说明与缺什么 */}
          {current && (
            <g>
              <text
                x={0}
                y={NODE_H + 24}
                fontSize={11}
                fill="var(--qm-text-dim)"
              >
                {qubitName}：{currentDescription}
              </text>
              {nextAction && (
                <text
                  x={0}
                  y={NODE_H + 42}
                  fontSize={11}
                  fill="var(--qm-accent)"
                >
                  下一步：{nextAction}
                </text>
              )}
              {missing.length > 0 && (
                <text x={0} y={NODE_H + 62} fontSize={11} fill="var(--qm-warn)">
                  还缺证据：
                  {missing
                    .map(
                      (m) =>
                        `${m.key}${
                          m.produced_by?.length
                            ? `（${m.produced_by.join(", ")}）`
                            : ""
                        }`
                    )
                    .join("；")}
                </text>
              )}
              {missing.length === 0 && (
                <text x={0} y={NODE_H + 62} fontSize={11} fill="var(--qm-ok)">
                  证据齐备，无缺失项
                </text>
              )}
            </g>
          )}
        </svg>
      </div>

      {/* 证据来源表：每个状态"再往前一步"该跑哪个实验 */}
      {Object.keys(missingByState).length > 0 && (
        <details
          style={{
            background: "var(--qm-panel)",
            border: "1px solid var(--qm-border)",
            borderRadius: "0.5rem",
            padding: "0.5rem 0.6rem",
          }}
        >
          <summary
            style={{
              cursor: "pointer",
              fontSize: "0.8rem",
              color: "var(--qm-text-dim)",
            }}
          >
            状态 → 缺失证据 → 产出实验（来自后端状态机表）
          </summary>
          <div style={{ marginTop: "0.5rem", display: "grid", gap: "0.35rem" }}>
            {Object.entries(missingByState).map(([st, keys]) => (
              <div
                key={st}
                style={{
                  display: "flex",
                  gap: "0.5rem",
                  alignItems: "baseline",
                  fontSize: "0.78rem",
                  flexWrap: "wrap",
                }}
              >
                <span
                  style={{
                    color: st === current ? "var(--qm-accent)" : "var(--qm-text)",
                    fontWeight: st === current ? 700 : 500,
                    minWidth: "10rem",
                  }}
                >
                  {st}
                </span>
                {keys.length === 0 ? (
                  <span style={{ color: "var(--qm-ok)" }}>已完成</span>
                ) : (
                  keys.map((k) => (
                    <span
                      key={k}
                      style={{
                        color: "var(--qm-warn)",
                        background: "var(--qm-warn-soft)",
                        borderRadius: "0.25rem",
                        padding: "0.05rem 0.3rem",
                      }}
                      title={evidenceSources[k]?.note ?? ""}
                    >
                      {k} ← {(evidenceSources[k]?.produced_by ?? []).join(", ") || "?"}
                    </span>
                  ))
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

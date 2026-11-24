"use client";

import { useMemo } from "react";

import {
  QubitState,
  QubitStateTable,
  StateMachineEdge,
} from "@/lib/controlApi";

/**
 * 测控状态转移图（BACKLOG O6）。
 *
 * 口径全部来自后端，前端不硬编码状态机：
 *   - 状态序列 / 说明 / 下一步 / 证据来源 / **三类边** / **三道闸门** / 每态 TTL
 *       → GET  /api/quantum/qubit/state-table   （services/common/state_machine.py）
 *   - 当前比特实际处于哪一态、A(s)、过期时间、还缺哪些证据
 *       → POST /api/quantum/qubit/state
 *
 * 图例（设计稿 §5）：
 *   - 前向 `forward` —— 实线箭头，从左到右推进；
 *   - 自环 `self`    —— 节点上方的灰虚线弧（重扫 / 重扫脉幅 / 重定位，有 max_attempts 上限）；
 *   - 回退 `back`    —— 节点下方的红虚线弧（证据失效时退回去重做）。
 * 当前态高亮；当前态的合法边 A(s) 用强调色加粗，其余边降透明度。
 */

interface Props {
  table: QubitStateTable | null;
  state: QubitState | null;
  qubitName: string;
}

const NODE_W = 132;
const NODE_H = 54;
const GAP = 28;
/** 顶部留给自环弧和前向跳转弧 */
const TOP_PAD = 56;
/** 底部留给回退弧 */
const BOTTOM_PAD = 26;
const BACK_DEPTH = 26;
const BACK_STEP = 17;

const KIND_LABEL: Record<string, string> = {
  forward: "前向",
  self: "自环",
  back: "回退",
};

const KIND_COLOR: Record<string, string> = {
  forward: "var(--qm-border-strong)",
  self: "var(--qm-text-mute)",
  back: "var(--qm-err)",
};

const KIND_DASH: Record<string, string | undefined> = {
  forward: undefined,
  self: "4 3",
  back: "5 3",
};

function shortFn(fn?: string): string {
  if (!fn) return "";
  return fn.replace(/^sq\./, "");
}

function fmtTime(ts?: number | null): string {
  if (ts === null || ts === undefined) return "—";
  const ms = ts > 1e12 ? ts : ts * 1000;
  return new Date(ms).toLocaleString();
}

/** 「还剩多久」——只用来看个大概，真正判定在后端 GATE-TTL。 */
function fmtRemaining(expiresAt?: number | null): string {
  if (!expiresAt) return "永不过期";
  const ms = (expiresAt > 1e12 ? expiresAt : expiresAt * 1000) - Date.now();
  if (ms <= 0) return "已过期";
  const hours = Math.floor(ms / 3600000);
  if (hours >= 48) return `约 ${Math.floor(hours / 24)} 天`;
  if (hours >= 1) return `约 ${hours} 小时`;
  return `约 ${Math.max(1, Math.floor(ms / 60000))} 分钟`;
}

export default function QubitStateGraph({ table, state, qubitName }: Props) {
  const states = table?.states ?? [];
  const descriptions = table?.descriptions ?? {};
  const missingByState = table?.missing_by_state ?? {};
  const evidenceSources = table?.evidence_sources ?? {};
  const allEdges = table?.edges ?? [];
  const gates = table?.gates ?? {};
  const stateTtl = table?.state_ttl ?? {};

  // 后端把派生结果放在 `state` 对象里（见 controlApi.ts 的 QubitState 注释）
  const detail = state?.state ?? null;
  const current = detail?.state ?? "";
  const currentIndex = useMemo(() => states.indexOf(current), [states, current]);

  const missing = state?.plan?.missing ?? detail?.missing_detail ?? [];
  const nextAction = state?.plan?.next_action ?? detail?.next_action ?? "";
  const currentDescription = detail?.description ?? descriptions[current] ?? "";

  /** A(s)：当前态下允许的边（后端 edges_for 的直接输出，前端不推算）。 */
  const legalEdges = state?.edges ?? [];
  const legalIds = useMemo(
    () => new Set(legalEdges.map((edge) => edge.id)),
    [legalEdges]
  );

  const expired = state?.valid === false;
  const validityReason = state?.validity?.reason ?? "";

  const geometry = useMemo(() => {
    const index = (name?: string) => (name ? states.indexOf(name) : -1);
    const x = (i: number) => i * (NODE_W + GAP);
    const y = TOP_PAD;

    const backEdges = allEdges.filter((edge) => edge.kind === "back");
    const backDepth = new Map<string, number>();
    backEdges.forEach((edge, i) => backDepth.set(edge.id, BACK_DEPTH + i * BACK_STEP));

    const maxBack = backEdges.length > 0
      ? BACK_DEPTH + (backEdges.length - 1) * BACK_STEP
      : 0;

    return {
      index,
      x,
      y,
      backDepth,
      width: Math.max(states.length * NODE_W + (states.length - 1) * GAP, 320),
      height: TOP_PAD + NODE_H + maxBack + BOTTOM_PAD,
    };
  }, [allEdges, states]);

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

  const { index, x, y, backDepth, width, height } = geometry;

  const edgeStroke = (edge: StateMachineEdge) => {
    const legal = legalIds.has(edge.id);
    if (legal && current) return "var(--qm-accent)";
    return KIND_COLOR[edge.kind ?? "forward"] ?? KIND_COLOR.forward;
  };

  const edgeOpacity = (edge: StateMachineEdge) => {
    if (!current) return 1;
    return legalIds.has(edge.id) ? 1 : 0.32;
  };

  const markerFor = (edge: StateMachineEdge) => {
    if (legalIds.has(edge.id) && current) return "url(#qm-edge-arrow-active)";
    if (edge.kind === "back") return "url(#qm-edge-arrow-back)";
    if (edge.kind === "self") return "url(#qm-edge-arrow-mute)";
    return "url(#qm-edge-arrow)";
  };

  /** 边上的小标签（自环 / 回退 / 跳跃前向才画，相邻前向由下方表格说明）。 */
  const renderLabel = (text: string, lx: number, ly: number, fill: string) => (
    <text x={lx} y={ly} textAnchor="middle" fontSize={9} fill={fill}>
      {text}
    </text>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
      {/* 时效横幅：过期是 GATE-TTL 会拦动作的唯一信号，放最上面 */}
      {state && (
        <div
          style={{
            display: "flex",
            gap: "0.5rem",
            alignItems: "center",
            flexWrap: "wrap",
            padding: "0.4rem 0.6rem",
            borderRadius: "0.4rem",
            fontSize: "0.78rem",
            background: expired ? "var(--qm-err-soft)" : "var(--qm-ok-soft)",
            border: `1px solid ${expired ? "var(--qm-err)" : "var(--qm-ok)"}`,
            color: expired ? "var(--qm-err)" : "var(--qm-ok)",
          }}
        >
          <strong>{expired ? "状态已过期" : "状态有效"}</strong>
          <span style={{ color: "var(--qm-text-dim)" }}>
            有效期至 {fmtTime(state.expires_at)}（{fmtRemaining(state.expires_at)}）
          </span>
          {expired && (
            <span style={{ color: "var(--qm-err)" }}>
              GATE-TTL 会拦截动作：先重判状态再下发
              {validityReason ? ` · ${validityReason}` : ""}
            </span>
          )}
          <span style={{ marginLeft: "auto", color: "var(--qm-text-mute)" }}>
            结构 v{table?.state_version ?? "—"} · 判据 v
            {detail?.criteria_version ?? "—"}
          </span>
        </div>
      )}

      <div style={{ overflowX: "auto", paddingBottom: "0.25rem" }}>
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="测控状态转移图（含前向 / 自环 / 回退三类边）"
          style={{ minWidth: width, display: "block" }}
        >
          <defs>
            {[
              ["qm-edge-arrow", "var(--qm-border-strong)"],
              ["qm-edge-arrow-active", "var(--qm-accent)"],
              ["qm-edge-arrow-mute", "var(--qm-text-mute)"],
              ["qm-edge-arrow-back", "var(--qm-err)"],
            ].map(([id, fill]) => (
              <marker
                key={id}
                id={id}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={fill} />
              </marker>
            ))}
          </defs>

          {/* ① 回退边：节点下方往下弯的弧，跨多态时深度递增避免重叠 */}
          {allEdges
            .filter((edge) => edge.kind === "back")
            .map((edge) => {
              const fi = index(edge.from);
              const ti = index(edge.to);
              if (fi < 0 || ti < 0) return null;
              const depth = backDepth.get(edge.id) ?? BACK_DEPTH;
              const sx = x(fi) + NODE_W / 2;
              const tx = x(ti) + NODE_W / 2;
              const baseY = y + NODE_H;
              const cx = (sx + tx) / 2;
              return (
                <g key={edge.id} opacity={edgeOpacity(edge)}>
                  <title>
                    {`${edge.id}｜${edge.kind}｜${shortFn(edge.action?.fn)}${
                      edge.note ? `｜${edge.note}` : ""
                    }`}
                  </title>
                  <path
                    d={`M ${sx} ${baseY} Q ${cx} ${baseY + depth * 2} ${tx} ${baseY + 2}`}
                    fill="none"
                    stroke={edgeStroke(edge)}
                    strokeWidth={legalIds.has(edge.id) && current ? 2.2 : 1.3}
                    strokeDasharray={KIND_DASH.back}
                    markerEnd={markerFor(edge)}
                  />
                  {renderLabel(
                    shortFn(edge.action?.fn),
                    cx,
                    baseY + depth + 12,
                    legalIds.has(edge.id) && current
                      ? "var(--qm-accent)"
                      : "var(--qm-err)"
                  )}
                </g>
              );
            })}

          {/* ② 前向边：相邻两态画直线，跳态画上方弧线 */}
          {allEdges
            .filter((edge) => (edge.kind ?? "forward") === "forward")
            .map((edge) => {
              const fi = index(edge.from);
              const ti = index(edge.to);
              if (fi < 0 || ti < 0) return null;
              const legal = legalIds.has(edge.id) && !!current;
              const stroke = edgeStroke(edge);
              const width2 = legal ? 2.4 : 1.4;
              const opacity = edgeOpacity(edge);
              if (ti === fi + 1) {
                const x1 = x(fi) + NODE_W;
                const x2 = x(ti);
                const midY = y + NODE_H / 2;
                return (
                  <g key={edge.id} opacity={opacity}>
                    <title>{`${edge.id}｜forward｜${shortFn(edge.action?.fn)}`}</title>
                    <line
                      x1={x1}
                      y1={midY}
                      x2={x2 - 4}
                      y2={midY}
                      stroke={stroke}
                      strokeWidth={width2}
                      markerEnd={markerFor(edge)}
                    />
                  </g>
                );
              }
              // 跳态前向（如 unknown → 后面某态）：上方弧线，避开节点
              const sx = x(fi) + NODE_W / 2;
              const tx = x(ti) + NODE_W / 2;
              const cx = (sx + tx) / 2;
              const cy = y - 30;
              return (
                <g key={edge.id} opacity={opacity}>
                  <title>{`${edge.id}｜forward｜${shortFn(edge.action?.fn)}`}</title>
                  <path
                    d={`M ${sx} ${y} Q ${cx} ${cy} ${tx} ${y - 4}`}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={width2}
                    markerEnd={markerFor(edge)}
                  />
                  {renderLabel(shortFn(edge.action?.fn), cx, cy + 2, stroke)}
                </g>
              );
            })}

          {/* ③ 自环：节点上方的小弧，灰虚线 */}
          {allEdges
            .filter((edge) => edge.kind === "self")
            .map((edge) => {
              const fi = index(edge.from);
              if (fi < 0) return null;
              const legal = legalIds.has(edge.id) && !!current;
              const stroke = legal ? "var(--qm-accent)" : KIND_COLOR.self;
              const sx = x(fi) + NODE_W * 0.3;
              const tx = x(fi) + NODE_W * 0.7;
              const cy = y - 34;
              return (
                <g key={edge.id} opacity={edgeOpacity(edge)}>
                  <title>
                    {`${edge.id}｜self｜${shortFn(edge.action?.fn)}${
                      edge.max_attempts ? `｜最多 ${edge.max_attempts} 次` : ""
                    }${edge.note ? `｜${edge.note}` : ""}`}
                  </title>
                  <path
                    d={`M ${sx} ${y - 2} Q ${x(fi) + NODE_W / 2} ${cy} ${tx} ${y - 2}`}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={legal ? 2.2 : 1.2}
                    strokeDasharray={KIND_DASH.self}
                    markerEnd={markerFor(edge)}
                  />
                  {renderLabel(
                    shortFn(edge.action?.fn),
                    x(fi) + NODE_W / 2,
                    cy - 2,
                    legal ? "var(--qm-accent)" : KIND_COLOR.self
                  )}
                </g>
              );
            })}

          {/* ④ 状态节点 */}
          {states.map((s, i) => {
            const nodeX = x(i);
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
            const ttl = stateTtl[s];
            return (
              <g key={s} opacity={opacity}>
                <title>{`${s}｜${descriptions[s] ?? ""}${
                  ttl ? `｜有效期 ${Math.round(ttl / 3600)} 小时` : "｜永不过期"
                }`}</title>
                <rect
                  x={nodeX}
                  y={y}
                  width={NODE_W}
                  height={NODE_H}
                  rx={8}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={isCurrent ? 2 : 1}
                  strokeDasharray={reached || isCurrent ? undefined : "4 3"}
                />
                <text
                  x={nodeX + NODE_W / 2}
                  y={y + NODE_H / 2 - 4}
                  textAnchor="middle"
                  fontSize={isCurrent ? 12 : 11}
                  fontWeight={isCurrent ? 700 : 500}
                  fill={isCurrent ? "var(--qm-accent)" : "var(--qm-text)"}
                >
                  {s}
                </text>
                <text
                  x={nodeX + NODE_W / 2}
                  y={y + NODE_H / 2 + 13}
                  textAnchor="middle"
                  fontSize={9}
                  fill="var(--qm-text-mute)"
                >
                  {isCurrent ? "当前状态" : reached ? "已达成" : "未达到"}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* 图例 + 当前态摘要 */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "0.4rem",
          fontSize: "0.76rem",
          color: "var(--qm-text-dim)",
        }}
      >
        <div style={{ display: "flex", gap: "0.9rem", flexWrap: "wrap" }}>
          {(["forward", "self", "back"] as const).map((kind) => (
            <span
              key={kind}
              style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}
            >
              <svg width={26} height={8} aria-hidden="true">
                <line
                  x1={0}
                  y1={4}
                  x2={24}
                  y2={4}
                  stroke={KIND_COLOR[kind]}
                  strokeWidth={kind === "forward" ? 2 : 1.6}
                  strokeDasharray={KIND_DASH[kind]}
                />
              </svg>
              {KIND_LABEL[kind]}
              <span style={{ color: "var(--qm-text-mute)" }}>
                {allEdges.filter((e) => (e.kind ?? "forward") === kind).length} 条
              </span>
            </span>
          ))}
        </div>

        {current && (
          <div style={{ color: "var(--qm-text-dim)" }}>
            <span style={{ color: "var(--qm-accent)", fontWeight: 600 }}>
              {qubitName}
            </span>
            ：{currentDescription}
            {nextAction ? ` · 下一步：${nextAction}` : ""}
          </div>
        )}
        {current && (
          <div style={{ color: missing.length > 0 ? "var(--qm-warn)" : "var(--qm-ok)" }}>
            {missing.length > 0
              ? `还缺证据：${missing
                  .map(
                    (m) =>
                      `${m.key}${
                        m.produced_by?.length ? `（${m.produced_by.join(", ")}）` : ""
                      }`
                  )
                  .join("；")}`
              : "证据齐备，无缺失项"}
          </div>
        )}
      </div>

      {/* 闸门：三道闸门是动作下发前的硬条件，配置也来自后端 */}
      {Object.keys(gates).length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.35rem",
            alignItems: "center",
            fontSize: "0.75rem",
          }}
        >
          <span style={{ color: "var(--qm-text-mute)" }}>闸门：</span>
          {Object.entries(gates).map(([key, gate]) => {
            const blocking =
              gate.on_violation === "block" || gate.on_version_mismatch === "block";
            return (
              <span
                key={key}
                title={gate.description ?? ""}
                style={{
                  padding: "0.1rem 0.4rem",
                  borderRadius: "0.25rem",
                  border: `1px solid ${
                    blocking ? "var(--qm-warn)" : "var(--qm-border)"
                  }`,
                  background: blocking ? "var(--qm-warn-soft)" : "var(--qm-panel)",
                  color: blocking ? "var(--qm-warn)" : "var(--qm-text-dim)",
                }}
              >
                {gate.id ?? key}
                <span style={{ color: "var(--qm-text-mute)" }}>
                  {blocking ? " · block" : " · pass"}
                </span>
              </span>
            );
          })}
        </div>
      )}

      {/* 边的明细表：当前态 A(s) 优先，无当前态时给全表 */}
      <details
        open={!!current}
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
          {current
            ? `当前态 A(${current})：${legalEdges.length} 条合法转移（三类边）`
            : `状态机边表：${allEdges.length} 条（三类边）`}
        </summary>
        <div style={{ marginTop: "0.5rem", display: "grid", gap: "0.35rem" }}>
          {(current ? legalEdges : allEdges).map((edge) => {
            const kind = edge.kind ?? "forward";
            const fns = [edge.action?.fn, ...(edge.action?.alternatives ?? [])].filter(
              Boolean
            ) as string[];
            return (
              <div
                key={edge.id}
                style={{
                  display: "flex",
                  gap: "0.45rem",
                  alignItems: "baseline",
                  flexWrap: "wrap",
                  fontSize: "0.76rem",
                }}
              >
                <span
                  style={{
                    minWidth: "3.2rem",
                    color: KIND_COLOR[kind],
                    fontWeight: 600,
                  }}
                >
                  {KIND_LABEL[kind] ?? kind}
                </span>
                <span style={{ color: "var(--qm-text)", minWidth: "16rem" }}>
                  {edge.from ?? "?"} → {edge.to ?? "?"}
                  <span style={{ color: "var(--qm-text-mute)" }}> （{edge.id}）</span>
                </span>
                <span style={{ color: "var(--qm-accent)" }}>
                  {fns.map(shortFn).join(" / ")}
                </span>
                {(edge.requires ?? []).length > 0 && (
                  <span style={{ color: "var(--qm-warn)" }}>
                    需 {edge.requires?.join(", ")}
                  </span>
                )}
                {edge.kind === "self" && typeof edge.max_attempts === "number" && (
                  <span style={{ color: "var(--qm-text-mute)" }}>
                    最多 {edge.max_attempts} 次
                  </span>
                )}
                {edge.note && (
                  <span style={{ color: "var(--qm-text-mute)" }}>{edge.note}</span>
                )}
              </div>
            );
          })}
          {(current ? legalEdges : allEdges).length === 0 && (
            <div style={{ fontSize: "0.76rem", color: "var(--qm-text-mute)" }}>
              没有登记任何边（检查 config/state_machine.json）
            </div>
          )}
        </div>
      </details>

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

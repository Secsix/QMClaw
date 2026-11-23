"use client";

import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  type Node,
  type Edge,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { RunNode, WorkflowCanvas, WorkflowRun } from "@/lib/controlApi";

/**
 * QWF 流程图（只读）。
 *
 * 数据来源：
 *   - 拓扑（节点/连线/位置）来自 GET /api/workflows/:id 的 `canvas`（后端 ir_to_canvas 生成）
 *   - 节点运行状态来自 GET /api/workflow-runs 里当前 run 的 `nodes[].status`
 *
 * 状态着色：completed=绿、running=蓝（闪烁）、failed=红、skipped/pending=灰。
 * 画布节点类型与工作台编辑器无关，这里用自绘的只读节点，避免拉起编辑器那套 store。
 */

interface Props {
  canvas: WorkflowCanvas | null;
  run: WorkflowRun | null;
  height?: number;
}

interface FlowData extends Record<string, unknown> {
  label: string;
  status: string;
  nodeType: string;
  running: boolean;
  duration?: number | null;
}

function statusColor(status: string): { border: string; bg: string; text: string } {
  const s = (status || "").toLowerCase();
  if (s.includes("run")) return { border: "var(--qm-accent)", bg: "var(--qm-accent-soft)", text: "var(--qm-accent)" };
  if (s.includes("fail") || s.includes("error"))
    return { border: "var(--qm-err)", bg: "var(--qm-err-soft)", text: "var(--qm-err)" };
  if (s.includes("complete") || s.includes("success") || s.includes("done"))
    return { border: "var(--qm-ok)", bg: "var(--qm-ok-soft)", text: "var(--qm-ok)" };
  if (s.includes("skip"))
    return { border: "var(--qm-border-strong)", bg: "var(--qm-panel-2)", text: "var(--qm-text-mute)" };
  return { border: "var(--qm-border)", bg: "var(--qm-panel)", text: "var(--qm-text-dim)" };
}

function FlowNode({ data }: NodeProps) {
  const d = data as FlowData;
  const c = statusColor(d.status);
  const running = d.running;
  return (
    <div
      style={{
        minWidth: 150,
        maxWidth: 220,
        padding: "0.45rem 0.6rem",
        borderRadius: "0.4rem",
        border: `1px solid ${c.border}`,
        background: c.bg,
        color: "var(--qm-text)",
        fontSize: "0.75rem",
        lineHeight: 1.35,
        animation: running ? "qm-pulse 1.1s ease-in-out infinite" : undefined,
      }}
    >
      <div style={{ fontWeight: 600, wordBreak: "break-word" }}>
        {d.label || d.nodeType}
      </div>
      <div style={{ color: c.text, fontSize: "0.68rem", marginTop: "0.15rem" }}>
        {d.nodeType} · {d.status || "pending"}
      </div>
    </div>
  );
}

const nodeTypes = { flowNode: FlowNode };

export default function QwfFlowPanel({ canvas, run, height = 320 }: Props) {
  const statusByNode = useMemo(() => {
    const map = new Map<string, RunNode>();
    (run?.nodes ?? []).forEach((n) => map.set(n.nodeId, n));
    return map;
  }, [run?.nodes]);

  const { nodes, edges } = useMemo(() => {
    if (!canvas) return { nodes: [] as Node[], edges: [] as Edge[] };

    const ns: Node[] = (canvas.nodes ?? []).map((n, idx) => {
      const rn = statusByNode.get(n.id);
      const status = rn?.status ?? "pending";
      const label =
        n.label ||
        (n.dsl?.as ? `${n.dsl.as}` : n.dsl?.op || n.type || n.id);
      return {
        id: n.id,
        type: "flowNode",
        position: n.position ?? { x: 0, y: idx * 90 },
        data: {
          label,
          status,
          nodeType: n.type || n.dsl?.op || "node",
          running: (status || "").toLowerCase().includes("run"),
          duration: rn?.duration ?? null,
        } satisfies FlowData,
      };
    });

    const es: Edge[] = (canvas.edges ?? []).map((e, idx) => ({
      id: e.id || `edge-${idx}`,
      source: e.source,
      target: e.target,
      animated: false,
      style: { stroke: "var(--qm-border-strong)", strokeWidth: 1.4 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--qm-border-strong)" },
    }));

    return { nodes: ns, edges: es };
  }, [canvas, statusByNode]);

  if (!canvas || (canvas.nodes ?? []).length === 0) {
    return (
      <div
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--qm-text-mute)",
          fontSize: "0.82rem",
          background: "var(--qm-panel)",
          border: "1px solid var(--qm-border)",
          borderRadius: "0.5rem",
        }}
      >
        暂无流程图（当前运行没有关联的 QWF 画布）
      </div>
    );
  }

  return (
    <div
      style={{
        height,
        background: "var(--qm-panel)",
        border: "1px solid var(--qm-border)",
        borderRadius: "0.5rem",
        overflow: "hidden",
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
      >
        <Background
          gap={16}
          size={1}
          color="var(--qm-border)"
          style={{ background: "var(--qm-bg-2)" }}
        />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
      <style>{`
        @keyframes qm-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.45; }
        }
        .react-flow__controls button {
          background: var(--qm-panel-2);
          border-bottom: 1px solid var(--qm-border);
          fill: var(--qm-text);
        }
        .react-flow__controls button:hover { background: var(--qm-panel-3); }
      `}</style>
    </div>
  );
}

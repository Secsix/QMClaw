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

import { IconQwfFlow } from "./icons/ControlIcons";

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
  /** 画布高度；栅格里传 "100%" 让它跟着卡片大小走 */
  height?: number | string;
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
      className="qm-flow-node"
      style={{
        borderColor: c.border,
        background: c.bg,
        animation: running ? "qm-pulse 1.1s ease-in-out infinite" : undefined,
      }}
    >
      <div className="qm-flow-node-title">{d.label || d.nodeType}</div>
      <div className="qm-flow-node-meta" style={{ color: c.text }}>
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

  const { nodes, edges, fallback } = useMemo(() => {
    const ns: Node[] = [];
    const es: Edge[] = [];
    const canvasNodes = canvas?.nodes ?? [];

    if (canvasNodes.length > 0) {
      canvasNodes.forEach((n, idx) => {
        const rn = statusByNode.get(n.id);
        const status = rn?.status ?? "pending";
        const label =
          n.label ||
          (n.dsl?.as ? `${n.dsl.as}` : n.dsl?.op || n.type || n.id);
        ns.push({
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
        });
      });

      (canvas?.edges ?? []).forEach((e, idx) => {
        es.push({
          id: e.id || `edge-${idx}`,
          source: e.source,
          target: e.target,
          animated: false,
          style: { stroke: "var(--qm-border-strong)", strokeWidth: 1.4 },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: "var(--qm-border-strong)",
          },
        });
      });

      return { nodes: ns, edges: es, fallback: false };
    }

    // 画布拿不到（run 的 workflowId 是一次性临时 id）→ 回退用运行节点画链，
    // 至少保证「当前跑了哪些实验、每个节点什么状态」可见。
    const runNodes = run?.nodes ?? [];
    runNodes.forEach((rn, idx) => {
      const cfg = (rn.input?.config ?? {}) as Record<string, unknown>;
      const fn = typeof cfg.fn === "string" ? cfg.fn : "";
      const qubit = typeof cfg.qubit === "string" ? cfg.qubit : "";
      const label = fn
        ? `${fn}${qubit ? ` · ${qubit}` : ""}`
        : rn.nodeId || `node-${idx + 1}`;
      ns.push({
        id: rn.nodeId || `run-node-${idx}`,
        type: "flowNode",
        position: { x: 0, y: idx * 90 },
        data: {
          label,
          status: rn.status || "pending",
          nodeType: rn.nodeType || "node",
          running: (rn.status || "").toLowerCase().includes("run"),
          duration: rn.duration ?? null,
        } satisfies FlowData,
      });
      if (idx > 0) {
        es.push({
          id: `run-edge-${idx}`,
          source: runNodes[idx - 1].nodeId || `run-node-${idx - 1}`,
          target: rn.nodeId || `run-node-${idx}`,
          animated: false,
          style: { stroke: "var(--qm-border-strong)", strokeWidth: 1.4 },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: "var(--qm-border-strong)",
          },
        });
      }
    });

    return { nodes: ns, edges: es, fallback: runNodes.length > 0 };
  }, [canvas, run, statusByNode]);

  if (nodes.length === 0) {
    return (
      <div className="qm-empty" style={{ height }}>
        <span className="qm-empty-icon">
          <IconQwfFlow size={30} />
        </span>
        <span className="qm-kicker">No canvas</span>
        暂无流程图（当前运行没有关联的 QWF 画布，也没有可回退的运行节点）
      </div>
    );
  }

  return (
    <div className="qm-flow-canvas" style={{ height }}>
      {fallback ? (
        <div
          className="qm-fallback-chip"
          title="这次运行用的是临时工作流，后端没有对应的 QWF 画布，这里按运行节点顺序回退绘制"
        >
          按运行节点回退绘制
        </div>
      ) : null}
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
          border-radius: 0;
          fill: var(--qm-text);
        }
        .react-flow__controls button:hover { background: var(--qm-panel-3); }
      `}</style>
    </div>
  );
}

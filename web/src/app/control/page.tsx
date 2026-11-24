"use client";

import dynamic from "next/dynamic";

// @xyflow/react 依赖浏览器 DOM，SSR 阶段会报 window 错误 —— 插件页整体走客户端渲染。
const ControlDashboard = dynamic(
  () => import("@/components/control/ControlDashboard"),
  {
    ssr: false,
    loading: () => (
      <div
        style={{
          minHeight: "100dvh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--qm-bg)",
          color: "var(--qm-text-dim)",
          fontSize: "0.85rem",
        }}
      >
        正在加载测控控制台…
      </div>
    ),
  }
);

export default function ControlPage() {
  return <ControlDashboard />;
}

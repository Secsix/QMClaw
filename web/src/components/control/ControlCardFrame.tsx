"use client";

import type { ReactNode } from "react";

/**
 * 卡片外壳：标题栏 + 内容区。
 *
 * 栅格格子给了固定像素高度，所以外壳必须 height:100% + 内部滚动，
 * 否则内容长的卡片（实验动态、运行记录）会把格子顶开。
 * 标题栏带 `qm-card-handle` 类，是栅格拖动的唯一把手 —— 图表内部（reactflow 画布、
 * 图片放大、滚动区）因此不会被拖动抢走手势。
 */

export interface ControlCardFrameProps {
  title: string;
  subtitle?: ReactNode;
  right?: ReactNode;
  /** 编辑态：显示隐藏按钮、标题栏变成 move 光标 */
  editing?: boolean;
  onHide?: () => void;
  children: ReactNode;
}

export default function ControlCardFrame({
  title,
  subtitle,
  right,
  editing = false,
  onHide,
  children,
}: ControlCardFrameProps) {
  return (
    <section
      className="qm-card-frame"
      style={{
        height: "100%",
        minHeight: 0,
        background: "var(--qm-bg)",
        border: "1px solid var(--qm-border)",
        borderRadius: "0.6rem",
        padding: "0.7rem 0.8rem",
        display: "flex",
        flexDirection: "column",
        gap: "0.55rem",
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      <header
        className="qm-card-handle"
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "0.5rem",
          flexWrap: "wrap",
          flexShrink: 0,
        }}
      >
        <h2 style={{ margin: 0, fontSize: "0.9rem", color: "var(--qm-text)" }}>{title}</h2>
        {subtitle ? (
          <span style={{ fontSize: "0.72rem", color: "var(--qm-text-mute)" }}>{subtitle}</span>
        ) : null}
        <span
          style={{
            marginLeft: "auto",
            display: "inline-flex",
            alignItems: "center",
            gap: "0.4rem",
          }}
        >
          {right}
          {editing && onHide ? (
            <button
              type="button"
              className="qm-card-hide qm-card-nodrag"
              onClick={onHide}
              title="隐藏这张卡片（可在编辑布局里恢复）"
            >
              ✕
            </button>
          ) : null}
        </span>
      </header>

      <div
        className="qm-card-body"
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          gap: "0.5rem",
          overflow: "auto",
        }}
      >
        {children}
      </div>
    </section>
  );
}

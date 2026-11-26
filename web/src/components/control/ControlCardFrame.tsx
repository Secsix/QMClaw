"use client";

import type { ReactNode } from "react";

/**
 * 卡片外壳：编号 + 小标题 + 标题栏 + 内容区（瑞士风卡片语言）。
 *
 * 栅格格子给了固定像素高度，所以外壳必须 height:100% + 内部滚动，
 * 否则内容长的卡片（实验动态、运行记录）会把格子顶开。
 * 标题栏带 `qm-card-handle` 类，是栅格拖动的唯一把手 —— 图表内部（reactflow 画布、
 * 图片放大、滚动区）因此不会被拖动抢走手势。
 */

export interface ControlCardFrameProps {
  title: string;
  /** 卡片编号（两位数，瑞士风的网格坐标） */
  index?: string;
  /** 标题上方的小标签（英文大写） */
  kicker?: string;
  /** 卡片图标（见 icons/ControlIcons.tsx） */
  icon?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  /** 编辑态：显示隐藏按钮、标题栏变成 move 光标 */
  editing?: boolean;
  onHide?: () => void;
  children: ReactNode;
}

export default function ControlCardFrame({
  title,
  index,
  kicker,
  icon,
  subtitle,
  right,
  editing = false,
  onHide,
  children,
}: ControlCardFrameProps) {
  return (
    <section className="qm-card-frame">
      <header className="qm-card-head qm-card-handle">
        {index ? <span className="qm-card-index">{index}</span> : null}
        {icon ? <span className="qm-card-icon">{icon}</span> : null}
        <div className="qm-card-heading">
          {kicker ? <span className="qm-kicker">{kicker}</span> : null}
          <h2 className="qm-card-title">{title}</h2>
        </div>
        {subtitle ? <span className="qm-card-sub">{subtitle}</span> : null}
        <span className="qm-card-tools">
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

      <div className="qm-card-body">{children}</div>
    </section>
  );
}

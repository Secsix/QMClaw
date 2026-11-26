"use client";

import { useMemo, type ReactNode } from "react";
import { Responsive, WidthProvider, type Layout, type Layouts } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import "./control-grid.css";
import "./control-swiss.css";

import ControlCardFrame from "./ControlCardFrame";
import {
  CONTROL_BREAKPOINTS,
  CONTROL_BREAKPOINT_KEYS,
  CONTROL_CARDS,
  CONTROL_COLS,
  GRID_MARGIN,
  GRID_ROW_HEIGHT,
  type ControlCardId,
} from "./controlLayout";
import type { ControlLayoutApi } from "./useControlLayout";

const ResponsiveGrid = WidthProvider(Responsive);

export interface ControlCardContent {
  id: ControlCardId;
  subtitle?: ReactNode;
  right?: ReactNode;
  body: ReactNode;
}

interface Props {
  layout: ControlLayoutApi;
  /** 4 张卡片的内容（顺序无关，按 id 对应布局） */
  cards: ControlCardContent[];
}

const META = new Map(CONTROL_CARDS.map((c) => [c.id, c]));

/**
 * /control 的可配置栅格。
 *
 * 默认只读：只有点「编辑布局」之后才允许拖动/缩放/隐藏，避免看数据时误拖。
 * 布局按断点（lg/md/sm/xs）分别记忆，存在 localStorage（见 controlLayout.ts）。
 */
export default function ControlCardGrid({ layout, cards }: Props) {
  const { state, editing, ready } = layout;

  const visible = useMemo(
    () => cards.filter((c) => !state.hidden.includes(c.id)),
    [cards, state.hidden]
  );
  const hiddenCards = useMemo(
    () => CONTROL_CARDS.filter((c) => state.hidden.includes(c.id)),
    [state.hidden]
  );

  // 交给 RGL 的布局里去掉隐藏卡片（它们的位置仍留在 state 里，重新显示会回到原处）
  const gridLayouts = useMemo(() => {
    const out: Layouts = {};
    for (const bp of CONTROL_BREAKPOINT_KEYS) {
      out[bp] = state.layouts[bp]
        .filter((it) => !state.hidden.includes(it.i))
        .map((it) => {
          const meta = META.get(it.i);
          return {
            i: it.i,
            x: it.x,
            y: it.y,
            w: it.w,
            h: it.h,
            minW: meta?.minW ?? 2,
            minH: meta?.minH ?? 2,
          } as Layout;
        });
    }
    return out;
  }, [state.layouts, state.hidden]);

  return (
    <div>
      <div className="qm-layout-toolbar">
        <span className="qm-kicker">Layout</span>
        <button
          type="button"
          className="qm-btn"
          data-active={editing ? "true" : "false"}
          onClick={layout.toggleEditing}
        >
          {editing ? "✓ 完成编辑" : "⚙ 编辑布局"}
        </button>

        {editing ? (
          <>
            <span className="qm-layout-hint">
              拖卡片标题栏移动、拖右下角改大小；布局按当前屏幕宽度分别记忆，存在本机浏览器
            </span>
            <button type="button" className="qm-btn" onClick={layout.reset}>
              ↺ 恢复默认布局
            </button>
            {hiddenCards.length > 0 ? (
              <span className="qm-layout-chips">
                <span className="qm-layout-hint">已隐藏：</span>
                {hiddenCards.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    className="qm-btn"
                    onClick={() => layout.show(c.id)}
                    title={`显示「${c.title}」`}
                  >
                    ＋ {c.short}
                  </button>
                ))}
              </span>
            ) : null}
          </>
        ) : state.hidden.length > 0 ? (
          <span className="qm-layout-hint">
            已隐藏 {state.hidden.length} 张卡片（点「编辑布局」可恢复）
          </span>
        ) : null}
      </div>

      {!ready ? null : visible.length === 0 ? (
        <div className="qm-empty">
          <span className="qm-kicker">No cards</span>
          所有卡片都已隐藏 —— 点「编辑布局」→「＋ …」把它们加回来，或「恢复默认布局」。
        </div>
      ) : (
        <div className="qm-control-grid" data-editing={editing ? "true" : "false"}>
          <ResponsiveGrid
            layouts={gridLayouts}
            breakpoints={{ ...CONTROL_BREAKPOINTS }}
            cols={{ ...CONTROL_COLS }}
            rowHeight={GRID_ROW_HEIGHT}
            margin={GRID_MARGIN}
            containerPadding={[0, 0]}
            isDraggable={editing}
            isResizable={editing}
            draggableHandle=".qm-card-handle"
            draggableCancel=".qm-card-nodrag"
            compactType="vertical"
            preventCollision={false}
            useCSSTransforms
            onLayoutChange={layout.handleLayoutChange}
          >
            {visible.map((card) => {
              const meta = META.get(card.id);
              return (
                <div key={card.id} className="qm-grid-item">
                  <ControlCardFrame
                    title={meta?.title ?? card.id}
                    index={meta?.index}
                    kicker={meta?.kicker}
                    subtitle={card.subtitle}
                    right={card.right}
                    editing={editing}
                    onHide={() => layout.hide(card.id)}
                  >
                    {card.body}
                  </ControlCardFrame>
                </div>
              );
            })}
          </ResponsiveGrid>
        </div>
      )}
    </div>
  );
}

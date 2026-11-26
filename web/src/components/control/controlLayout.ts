"use client";

/**
 * /control 控制台卡片的可配置布局模型。
 *
 * 只存在浏览器 localStorage，不走后端：换浏览器/清缓存就回到默认布局。
 * 卡片本身由 ControlDashboard 注册（id → 标题 + 内容），这里只管"放哪儿、多大、显示不显示"。
 */

export type ControlCardId = "stateGraph" | "qwfFlow" | "liveExperiment" | "runs";

export interface ControlCardMeta {
  id: ControlCardId;
  /** 卡片标题（也出现在"隐藏的卡片"胶囊里） */
  title: string;
  /** 编辑模式下的短名 */
  short: string;
  /** 卡片头部的两位编号（瑞士风的网格坐标感） */
  index: string;
  /** 卡片头部的小标题（英文大写标签，作为标题之上的一层） */
  kicker: string;
  /** 缩放下限（栅格单位） */
  minW: number;
  minH: number;
}

export const CONTROL_CARDS: ControlCardMeta[] = [
  {
    id: "stateGraph",
    title: "测控状态转移图",
    short: "状态转移图",
    index: "01",
    kicker: "State Machine",
    minW: 3,
    minH: 6,
  },
  {
    id: "qwfFlow",
    title: "QWF 流程图",
    short: "QWF 流程图",
    index: "02",
    kicker: "QWF Flow",
    minW: 3,
    minH: 6,
  },
  {
    id: "liveExperiment",
    title: "实验动态",
    short: "实验动态",
    index: "03",
    kicker: "Live Experiment",
    minW: 4,
    minH: 8,
  },
  {
    id: "runs",
    title: "运行记录",
    short: "运行记录",
    index: "04",
    kicker: "Run History",
    minW: 2,
    minH: 4,
  },
];

export const CONTROL_CARD_IDS: ControlCardId[] = CONTROL_CARDS.map((c) => c.id);

/** 断点（沿用 react-grid-layout 的约定：取比当前宽度小的最大断点）。 */
export const CONTROL_BREAKPOINTS = { lg: 1200, md: 900, sm: 640, xs: 0 } as const;
export type ControlBreakpoint = keyof typeof CONTROL_BREAKPOINTS;
export const CONTROL_BREAKPOINT_KEYS: ControlBreakpoint[] = ["lg", "md", "sm", "xs"];

export const CONTROL_COLS: Record<ControlBreakpoint, number> = {
  lg: 12,
  md: 12,
  sm: 6,
  xs: 4,
};

/** 栅格行高与间距（像素）。卡片高度 = h * (rowHeight + marginY) - marginY。 */
export const GRID_ROW_HEIGHT = 28;
export const GRID_MARGIN: [number, number] = [10, 10];

export interface ControlLayoutItem {
  i: ControlCardId;
  x: number;
  y: number;
  w: number;
  h: number;
}

export type ControlLayouts = Record<ControlBreakpoint, ControlLayoutItem[]>;

export interface ControlLayoutState {
  version: 1;
  layouts: ControlLayouts;
  /** 被隐藏的卡片：位置仍保留在 layouts 里，重新显示时回到原处 */
  hidden: ControlCardId[];
}

export const DEFAULT_LAYOUTS: ControlLayouts = {
  lg: [
    { i: "stateGraph", x: 0, y: 0, w: 7, h: 15 },
    { i: "qwfFlow", x: 7, y: 0, w: 5, h: 15 },
    { i: "liveExperiment", x: 0, y: 15, w: 12, h: 13 },
    { i: "runs", x: 0, y: 28, w: 12, h: 8 },
  ],
  md: [
    { i: "stateGraph", x: 0, y: 0, w: 7, h: 15 },
    { i: "qwfFlow", x: 7, y: 0, w: 5, h: 15 },
    { i: "liveExperiment", x: 0, y: 15, w: 12, h: 13 },
    { i: "runs", x: 0, y: 28, w: 12, h: 8 },
  ],
  sm: [
    { i: "stateGraph", x: 0, y: 0, w: 6, h: 15 },
    { i: "qwfFlow", x: 0, y: 15, w: 6, h: 15 },
    { i: "liveExperiment", x: 0, y: 30, w: 6, h: 13 },
    { i: "runs", x: 0, y: 43, w: 6, h: 8 },
  ],
  xs: [
    { i: "stateGraph", x: 0, y: 0, w: 4, h: 15 },
    { i: "qwfFlow", x: 0, y: 15, w: 4, h: 15 },
    { i: "liveExperiment", x: 0, y: 30, w: 4, h: 13 },
    { i: "runs", x: 0, y: 43, w: 4, h: 8 },
  ],
};

const STORAGE_KEY = "qmclaw.control.layout.v1";

function cloneLayouts(src: ControlLayouts): ControlLayouts {
  const out = {} as ControlLayouts;
  for (const bp of CONTROL_BREAKPOINT_KEYS) {
    out[bp] = src[bp].map((it) => ({ ...it }));
  }
  return out;
}

export function defaultLayoutState(): ControlLayoutState {
  return { version: 1, layouts: cloneLayouts(DEFAULT_LAYOUTS), hidden: [] };
}

function clampItem(item: ControlLayoutItem, cols: number): ControlLayoutItem {
  const meta = CONTROL_CARDS.find((c) => c.id === item.i);
  const minW = meta?.minW ?? 2;
  const minH = meta?.minH ?? 2;
  const w = Math.min(cols, Math.max(minW, Math.round(item.w)));
  const h = Math.max(minH, Math.round(item.h));
  const x = Math.min(Math.max(0, Math.round(item.x)), Math.max(0, cols - w));
  const y = Math.max(0, Math.round(item.y));
  return { i: item.i, x, y, w, h };
}

/**
 * 把本地存档与当前卡片集合对齐：
 * - 存档里已不存在的卡片 → 丢弃
 * - 新加的卡片（存档里没有）→ 追加到该断点最下方
 * - 尺寸/坐标越界 → 夹回合法范围
 */
export function normalizeLayoutState(input: Partial<ControlLayoutState> | null | undefined): ControlLayoutState {
  const base = defaultLayoutState();
  if (!input || typeof input !== "object") return base;

  const layouts = {} as ControlLayouts;
  for (const bp of CONTROL_BREAKPOINT_KEYS) {
    const cols = CONTROL_COLS[bp];
    const stored = Array.isArray(input.layouts?.[bp]) ? input.layouts![bp]! : [];
    // 这个断点没有存档（第一次用、或旧版本没存过）→ 直接用默认布局
    if (stored.length === 0) {
      layouts[bp] = base.layouts[bp].map((it) => clampItem(it, cols));
      continue;
    }
    const byId = new Map<ControlCardId, ControlLayoutItem>();
    for (const raw of stored) {
      if (!raw || !CONTROL_CARD_IDS.includes(raw.i)) continue;
      byId.set(raw.i, clampItem(raw, cols));
    }
    // 缺失的卡片：按默认布局补到下方，避免重叠
    let cursor = byId.size
      ? Math.max(...Array.from(byId.values()).map((it) => it.y + it.h))
      : 0;
    for (const id of CONTROL_CARD_IDS) {
      if (byId.has(id)) continue;
      const fallback = base.layouts[bp].find((it) => it.i === id)!;
      byId.set(id, clampItem({ ...fallback, y: cursor }, cols));
      cursor += fallback.h;
    }
    layouts[bp] = CONTROL_CARD_IDS.map((id) => byId.get(id)!);
  }

  const hidden = Array.isArray(input.hidden)
    ? input.hidden.filter((id): id is ControlCardId => CONTROL_CARD_IDS.includes(id))
    : [];
  return { version: 1, layouts, hidden: Array.from(new Set(hidden)) };
}

export function loadLayoutState(): ControlLayoutState {
  if (typeof window === "undefined") return defaultLayoutState();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultLayoutState();
    return normalizeLayoutState(JSON.parse(raw) as Partial<ControlLayoutState>);
  } catch {
    // 坏数据（手改、旧版本）直接回到默认布局，不阻塞页面
    return defaultLayoutState();
  }
}

export function saveLayoutState(state: ControlLayoutState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // 存储被禁用/写满：布局只在本次会话生效
  }
}

export function layoutIsDefault(state: ControlLayoutState): boolean {  if (state.hidden.length > 0) return false;
  return CONTROL_BREAKPOINT_KEYS.every((bp) => {
    const cur = state.layouts[bp];
    const def = DEFAULT_LAYOUTS[bp];
    if (cur.length !== def.length) return false;
    return def.every((d) => {
      const c = cur.find((it) => it.i === d.i);
      return !!c && c.x === d.x && c.y === d.y && c.w === d.w && c.h === d.h;
    });
  });
}

/**
 * 把 react-grid-layout 回调里的布局并回存档（纯函数，方便单独验证）。
 *
 * - 只看可见卡片：栅格里没有隐藏卡片，它们的位置从 prev 原样搬过来
 * - 没有任何实际变化时返回同一个对象引用，避免多一次落盘与重渲染
 */
export function mergeGridLayouts(
  prev: ControlLayoutState,
  allLayouts: Partial<Record<string, ControlLayoutItem[]>>
): ControlLayoutState {
  let changed = false;
  const nextLayouts = {} as ControlLayouts;

  for (const bp of CONTROL_BREAKPOINT_KEYS) {
    const visible = (allLayouts?.[bp] ?? [])
      .filter((it) => it && CONTROL_CARD_IDS.includes(it.i))
      .map((it) => ({ i: it.i, x: it.x, y: it.y, w: it.w, h: it.h }));
    // 回调里没给这个断点（或被隐藏）时，保留原样
    const hiddenItems = prev.layouts[bp].filter((it) => prev.hidden.includes(it.i));
    const fromGrid = visible.length > 0 ? visible : prev.layouts[bp].filter((it) => !prev.hidden.includes(it.i));
    const merged = [...fromGrid, ...hiddenItems];
    nextLayouts[bp] = merged;

    const before = prev.layouts[bp];
    if (
      before.length !== merged.length ||
      merged.some((it) => {
        const old = before.find((b) => b.i === it.i);
        return !old || old.x !== it.x || old.y !== it.y || old.w !== it.w || old.h !== it.h;
      })
    ) {
      changed = true;
    }
  }

  return changed ? { ...prev, layouts: nextLayouts } : prev;
}

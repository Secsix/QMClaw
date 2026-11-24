"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Layout, Layouts } from "react-grid-layout";

import {
  type ControlBreakpoint,
  type ControlCardId,
  type ControlLayoutItem,
  type ControlLayoutState,
  defaultLayoutState,
  loadLayoutState,
  mergeGridLayouts,
  normalizeLayoutState,
  saveLayoutState,
} from "./controlLayout";

/** 卡片栅格布局的读写与编辑态：默认只读，点「编辑布局」才能拖/缩放/隐藏。 */
export interface ControlLayoutApi {
  state: ControlLayoutState;
  /** 编辑态（拖动、缩放、隐藏按钮只在编辑态生效） */
  editing: boolean;
  setEditing: (next: boolean) => void;
  toggleEditing: () => void;
  /** 本地存档是否已读完（读完之前不渲染栅格，避免用默认布局覆盖存档） */
  ready: boolean;
  isHidden: (id: ControlCardId) => boolean;
  hide: (id: ControlCardId) => void;
  show: (id: ControlCardId) => void;
  reset: () => void;
  /** 接 react-grid-layout 的 onLayoutChange */
  handleLayoutChange: (layout: Layout[], allLayouts: Layouts) => void;
}

const HIDDEN_SENTINEL: ControlCardId[] = [];

export function useControlLayout(): ControlLayoutApi {
  const [state, setState] = useState<ControlLayoutState>(defaultLayoutState);
  const [ready, setReady] = useState(false);
  const [editing, setEditing] = useState(false);
  // 非编辑态下 RGL 也会回调 onLayoutChange（挂载、断点切换、窗口缩放），
  // 那些回调只反映它的内部规整，不能当成用户改动写回存档。
  const editingRef = useRef(false);

  useEffect(() => {
    setState(loadLayoutState());
    setReady(true);
  }, []);

  useEffect(() => {
    editingRef.current = editing;
  }, [editing]);

  useEffect(() => {
    if (!ready) return;
    saveLayoutState(state);
  }, [ready, state]);

  const handleLayoutChange = useCallback((_layout: Layout[], allLayouts: Layouts) => {
    if (!editingRef.current) return;
    setState((prev) => mergeGridLayouts(prev, allLayouts as unknown as Record<string, ControlLayoutItem[]>));
  }, []);

  const hide = useCallback((id: ControlCardId) => {
    setState((prev) =>
      prev.hidden.includes(id) ? prev : { ...prev, hidden: [...prev.hidden, id] }
    );
  }, []);

  const show = useCallback((id: ControlCardId) => {
    setState((prev) =>
      prev.hidden.includes(id)
        ? { ...prev, hidden: prev.hidden.filter((h) => h !== id) }
        : prev
    );
  }, []);

  const reset = useCallback(() => {
    setEditing(false);
    setState((prev) => {
      const fresh = defaultLayoutState();
      // 保留编辑态之外无需保留任何东西，直接回到出厂布局
      return fresh;
    });
  }, []);

  const isHidden = useCallback(
    (id: ControlCardId) => (state.hidden ?? HIDDEN_SENTINEL).includes(id),
    [state.hidden]
  );

  return {
    state,
    editing,
    setEditing,
    toggleEditing: () => setEditing((v) => !v),
    ready,
    isHidden,
    hide,
    show,
    reset,
    handleLayoutChange,
  };
}

/** 供测试/调试：把当前存档态序列化成字符串。 */
export function describeLayout(state: ControlLayoutState, bp: ControlBreakpoint = "lg"): string {
  return state.layouts[bp]
    .map((it) => `${it.i}@${it.x},${it.y} ${it.w}x${it.h}`)
    .join(" | ");
}

export { normalizeLayoutState };

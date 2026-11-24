"use client";

/**
 * useViewport — 视口尺寸感知的响应式 hook
 *
 * 背景：web 全部使用 inline style（没有 CSS Module），inline style 无法写媒体查询，
 * 所以断点判断必须放在 JS 侧。这个 hook 把「窗口有多宽」变成组件能读到的状态。
 *
 * 断点：
 *   - NARROW  (≤560px)：IDE 侧栏 / 半个版面的典型宽度。单栏 + 左右侧栏转抽屉。
 *   - COMPACT (≤900px)：平板 / 分屏。左侧栏保留（更窄），右侧栏转抽屉。
 *   - 其余：保持原有宽屏三栏布局不变。
 *
 * 注意（SSR / hydration）：服务端渲染时 window 不可用，返回宽屏默认值（1440×900）。
 * 客户端**首次渲染必须用同一个常量**做 useState 初值——如果初值直接读 window.innerWidth，
 * 窄视口下客户端首帧会渲染窄布局（多出 ☰ 按钮），与服务端 HTML 不一致 → hydration mismatch。
 * 真实尺寸只在 useEffect 里读，代价是首屏「先宽后窄」轻微跳一下。
 */

import { useEffect, useState } from "react";

/** ≤ 此宽度视为「窄」（IDE 侧栏档） */
export const BREAKPOINT_NARROW = 560;
/** ≤ 此宽度视为「紧凑」（平板 / 分屏档） */
export const BREAKPOINT_COMPACT = 900;

export interface Viewport {
  /** 可视区域宽度（px） */
  width: number;
  /** 可视区域高度（px） */
  height: number;
  /** ≤ BREAKPOINT_NARROW */
  isNarrow: boolean;
  /** ≤ BREAKPOINT_COMPACT */
  isCompact: boolean;
}

/** 服务端与客户端首次渲染共用的默认视口（必须一致，否则 hydration mismatch） */
export const DEFAULT_VIEWPORT: Viewport = {
  width: 1440,
  height: 900,
  isNarrow: false,
  isCompact: false,
};

function readViewport(): Viewport {
  if (typeof window === "undefined") {
    return DEFAULT_VIEWPORT;
  }
  const width = window.innerWidth;
  const height = window.innerHeight;
  return {
    width,
    height,
    isNarrow: width <= BREAKPOINT_NARROW,
    isCompact: width <= BREAKPOINT_COMPACT,
  };
}

export default function useViewport(): Viewport {
  // 初值必须是常量：服务端渲染与客户端首帧都拿到 DEFAULT_VIEWPORT，
  // 真实尺寸在下面的 effect 里校正（readViewport 只在这里被调用）。
  const [viewport, setViewport] = useState<Viewport>(DEFAULT_VIEWPORT);

  useEffect(() => {
    let raf = 0;
    const update = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewport(readViewport()));
    };

    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);

    // 被嵌进 IDE webview / iframe 时，宿主改尺寸不一定触发 window resize，
    // 再用 ResizeObserver 盯一次根元素，保证抽屉/栅格能跟着宿主尺寸走。
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(update);
      ro.observe(document.documentElement);
    }

    update();

    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
      ro?.disconnect();
      cancelAnimationFrame(raf);
    };
  }, []);

  return viewport;
}

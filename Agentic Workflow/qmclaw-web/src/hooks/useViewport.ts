"use client";

/**
 * useViewport — 视口尺寸感知的响应式 hook
 *
 * 背景：qmclaw-web 全部使用 inline style（没有 CSS Module），inline style 无法写媒体查询，
 * 所以断点判断必须放在 JS 侧。这个 hook 把「窗口有多宽」变成组件能读到的状态。
 *
 * 断点：
 *   - NARROW  (≤560px)：IDE 侧栏 / 半个版面的典型宽度。单栏 + 左右侧栏转抽屉。
 *   - COMPACT (≤900px)：平板 / 分屏。左侧栏保留（更窄），右侧栏转抽屉。
 *   - 其余：保持原有宽屏三栏布局不变。
 *
 * 注意：SSR 阶段 window 不可用，这里返回宽屏默认值（1440），
 * 保证服务端与客户端首次渲染一致，不触发 hydration mismatch；挂载后再由 effect 校正。
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

function readViewport(): Viewport {
  if (typeof window === "undefined") {
    return { width: 1440, height: 900, isNarrow: false, isCompact: false };
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
  const [viewport, setViewport] = useState<Viewport>(readViewport);

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

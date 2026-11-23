"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { applyTheme, startThemeObserver, type ThemeName } from "./applyTheme";

const STORAGE_KEY = "qmclaw-theme";

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  setTheme: () => {},
  toggle: () => {},
});

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

/** 供 layout 的内联脚本使用，避免首屏闪烁 */
export const THEME_STORAGE_KEY = STORAGE_KEY;

export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>("dark");

  // 首次挂载：从 localStorage 读取真实主题
  useEffect(() => {
    let initial: ThemeName = "dark";
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "light" || saved === "dark") initial = saved;
    } catch {
      /* localStorage 不可用时保持深色 */
    }
    setThemeState(initial);
    document.documentElement.dataset.qmTheme = initial;
    applyTheme(initial);
    const stop = startThemeObserver();
    return stop;
  }, []);

  const setTheme = useCallback((next: ThemeName) => {
    setThemeState(next);
    document.documentElement.dataset.qmTheme = next;
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* 忽略写入失败 */
    }
    applyTheme(next);
  }, []);

  const toggle = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggle }}>{children}</ThemeContext.Provider>
  );
}

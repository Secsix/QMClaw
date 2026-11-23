"use client";

import { useTheme } from "@/theme/ThemeProvider";

/** 深色 / 浅色主题切换按钮（header 通用） */
export default function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      onClick={toggle}
      title={isDark ? "切换到浅色主题" : "切换到深色主题"}
      aria-label={isDark ? "切换到浅色主题" : "切换到深色主题"}
      data-qm-theme-toggle="true"
      style={{
        padding: compact ? "0.15rem 0.4rem" : "0.2rem 0.55rem",
        fontSize: compact ? "0.85rem" : "0.9rem",
        lineHeight: 1.2,
        background: "var(--qm-panel)",
        color: "var(--qm-text)",
        border: "1px solid var(--qm-border)",
        borderRadius: "0.3rem",
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {isDark ? "🌙" : "☀️"}
    </button>
  );
}

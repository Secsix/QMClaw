/**
 * legacyColorMap —— 旧组件 inline style 的颜色映射表
 *
 * 背景：QMClaw 前端 60+ 个组件全部用 inline style 写死颜色（无 CSS Module），
 * 因此 CSS 媒体查询 / CSS 变量都盖不住这些颜色（inline style 优先级高于 class）。
 * 主题切换只能靠「运行时重映射」：读元素的内联样式 → 按 **属性角色** 把颜色换成
 * 另一套色板 → 写回。
 *
 * 为什么要分角色（surface / text / border）：
 *   同一个十六进制在不同角色下语义不同。例如 #0f172a 既是「页面底色」，
 *   又是「高亮按钮上的文字色」——前者浅色主题要变亮，后者要保持深色。
 *   按 CSS 属性名区分角色即可无歧义：
 *     color            → text
 *     background*      → surface
 *     border* / outline* → border
 */

/** 底色 / 面板 / 柔和底色：深色 → 浅色 */
export const SURFACE_MAP: Record<string, string> = {
  "0f172a": "f1f5f9", // 页面底色
  "0b1220": "e2e8f0",
  "0a0f1a": "eef2f7",
  "1e293b": "ffffff", // 卡片 / 面板
  "1a1a2e": "ffffff",
  "1a2744": "dbeafe",
  "1a3d3d": "cffafe",
  "1a3d2e": "dcfce7",
  "1e3a5f": "dbeafe", // 蓝色柔和底
  "1e3a2f": "dcfce7", // 绿色柔和底
  "1e1b4b": "e0e7ff",
  "2d1f5e": "ede9fe",
  "2d2d1e": "fefce8",
  "2d1e1e": "fee2e2",
  "2d1a2e": "fdf2f8",
  "2e1a3d": "fdf2f8",
  "3a1a1a": "fee2e2",
  "3a1e1e": "fee2e2", // 红色柔和底
  "3a2a1a": "fef3c7", // 橙色柔和底
  "3d2e1a": "fef3c7",
  "3d1a2e": "fdf2f8",
  "334155": "e2e8f0", // 次级面板
  "334569": "e2e8f0",
  "374151": "e5e7eb",
  "475569": "e2e8f0",
  "4c0519": "fdf2f8",
  "450a0a": "fee2e2",
  "451a1a": "fee2e2",
  "422006": "fef3c7",
  "2a1113": "fee2e2",
  "78350f": "fef3c7",
  "92400e": "fef3c7",
  "7f1d1d": "fee2e2",
  "052e16": "dcfce7",
  "166534": "15803d",
  "991b1b": "b91c1c",
  "9333ea": "7c3aed",
  "0369a1": "075985",
};

/** 文字色：深色 → 浅色（浅色主题需要更深才够对比） */
export const TEXT_MAP: Record<string, string> = {
  "e2e8f0": "0f172a",
  "cbd5e1": "334155",
  "94a3b8": "475569",
  "64748b": "64748b",
  "38bdf8": "0369a1",
  "22c55e": "15803d",
  "f87171": "dc2626",
  "ef4444": "dc2626",
  "f59e0b": "b45309",
  "fbbf24": "b45309",
  "fcd34d": "b45309",
  "fde68a": "b45309",
  "fb923c": "c2410c",
  "f97316": "c2410c",
  "b45309": "b45309",
  "a78bfa": "6d28d9",
  "8b5cf6": "6d28d9",
  "6366f1": "4338ca",
  "3b82f6": "1d4ed8",
  "22d3ee": "0e7490",
  "06b6d4": "0e7490",
  "0ea5e9": "0369a1",
  "f472b6": "db2777",
  "ec4899": "db2777",
  "fca5a5": "b91c1c",
  "86efac": "15803d",
  "6ee7b7": "047857",
  "34d399": "047857",
  "4b5563": "4b5563",
};

/** 描边 / 分隔线：深色 → 浅色 */
export const BORDER_MAP: Record<string, string> = {
  "1e293b": "e2e8f0",
  "334155": "cbd5e1",
  "334569": "cbd5e1",
  "475569": "94a3b8",
  "0f172a": "cbd5e1",
  "64748b": "94a3b8",
  "94a3b8": "64748b",
  "38bdf8": "0284c7",
  "22c55e": "16a34a",
  "f87171": "dc2626",
  "ef4444": "dc2626",
  "f59e0b": "d97706",
  "6366f1": "4338ca",
  "8b5cf6": "6d28d9",
  "a78bfa": "6d28d9",
  "22d3ee": "0891b2",
  "ec4899": "db2777",
  "1e3a5f": "bfdbfe",
};

const HEX_RE = /#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/g;

function replaceHex(value: string, map: Record<string, string>): string {
  if (!value.includes("#")) return value;
  return value.replace(HEX_RE, (match, hex: string) => {
    const key = hex.length === 3
      ? hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2]
      : hex;
    const lower = key.toLowerCase();
    const target = map[lower];
    if (!target) return match;
    return `#${target}`;
  });
}

/** 按 CSS 属性名判断角色 */
export type ColorRole = "text" | "surface" | "border";

export function roleOfProperty(prop: string): ColorRole | null {
  const p = prop.toLowerCase();
  if (p === "color" || p === "caret-color" || p === "text-decoration-color") return "text";
  if (p.startsWith("background")) return "surface";
  if (p.startsWith("border") || p.startsWith("outline")) return "border";
  return null;
}

/**
 * 把一条内联样式值按角色做深色→浅色 / 浅色→深色映射。
 * @param value 原始值（作者写的深色值）
 * @param role  属性角色
 * @param toLight true = 深色→浅色；false = 浅色→深色（反向还原）
 */
export function mapStyleValue(value: string, role: ColorRole, toLight: boolean): string {
  const forward =
    role === "text" ? TEXT_MAP : role === "surface" ? SURFACE_MAP : BORDER_MAP;
  if (toLight) return replaceHex(value, forward);
  // 反向：从浅色值还原成深色值
  const reverse: Record<string, string> = {};
  for (const [from, to] of Object.entries(forward)) reverse[to] = from;
  return replaceHex(value, reverse);
}

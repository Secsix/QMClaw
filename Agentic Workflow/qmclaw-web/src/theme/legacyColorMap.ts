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
 *
 * 关键坑（浏览器实测）：
 *   CSSOM 会把 inline style 里的颜色**规范化成 rgb() / rgba()**。
 *   `el.style.background = "#0f172a"` 之后 `getPropertyValue("background-color")`
 *   返回的是 "rgb(15, 23, 42)"，不是 "#0f172a"。
 *   所以映射必须能识别 rgb()/rgba() 记号，不能只认 hex。
 *   另外 `style.item(i)` 返回的是**长写属性名**（background-color、
 *   border-top-color、border-top-width …），一个声明会展开成好几项。
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

/** 颜色记号：hex(#rgb / #rrggbb / #rrggbbaa) 或 rgb()/rgba() 函数 */
const COLOR_TOKEN_RE =
  /#([0-9a-fA-F]{3,8})\b|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?\s*(?:,\s*[\d.]+%?\s*)?\)/g;

function clamp255(v: number): number {
  return Math.max(0, Math.min(255, Math.round(v)));
}

function toHex2(v: number): string {
  return clamp255(v).toString(16).padStart(2, "0");
}

/** 把一个颜色记号解析成 { hex: 6 位小写, alpha: 2 位或 undefined }；解析不了返回 null */
function parseColorToken(token: string): { hex: string; alpha?: string } | null {
  if (token.charAt(0) === "#") {
    let body = token.slice(1).toLowerCase();
    if (body.length === 3) {
      body = body[0] + body[0] + body[1] + body[1] + body[2] + body[2];
    }
    if (body.length === 4) {
      const a = body[3] + body[3];
      body = body.slice(0, 3);
      body = body[0] + body[0] + body[1] + body[1] + body[2] + body[2];
      return { hex: body, alpha: a };
    }
    if (body.length === 8) return { hex: body.slice(0, 6), alpha: body.slice(6) };
    if (body.length === 6) return { hex: body };
    return null;
  }
  const nums = token.match(/[\d.]+/g);
  if (!nums || nums.length < 3) return null;
  const r = parseFloat(nums[0]);
  const g = parseFloat(nums[1]);
  const b = parseFloat(nums[2]);
  if (!isFinite(r) || !isFinite(g) || !isFinite(b)) return null;
  const hex = toHex2(r) + toHex2(g) + toHex2(b);
  if (nums.length >= 4) {
    const a = parseFloat(nums[3]);
    if (isFinite(a) && a < 1) return { hex, alpha: toHex2(a * 255) };
  }
  return { hex };
}

/**
 * 把一条样式值里的所有颜色记号规范化成小写 hex，便于比较与查表。
 * 没有颜色记号时返回 null。
 */
export function normalizeColorValue(value: string): string | null {
  if (!value) return null;
  let found = false;
  const out = value.replace(COLOR_TOKEN_RE, (match) => {
    const parsed = parseColorToken(match);
    if (!parsed) return match;
    found = true;
    return `#${parsed.hex}${parsed.alpha ?? ""}`;
  });
  return found ? out : null;
}

/** 取一条样式值里的第一个颜色记号，返回小写 hex（无 alpha）；取不到返回 null */
export function firstColorHex(value: string): string | null {
  if (!value) return null;
  const m = value.match(COLOR_TOKEN_RE);
  if (!m) return null;
  const parsed = parseColorToken(m[0]);
  return parsed ? parsed.hex : null;
}

/** 相对亮度（WCAG）；用于判断「底色是否已经变亮」 */
export function isLightColor(hex: string): boolean {
  const h = hex.replace("#", "");
  if (h.length < 6) return false;
  const ch = (i: number) => parseInt(h.slice(i, i + 2), 16) / 255;
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const r = lin(ch(0));
  const g = lin(ch(2));
  const b = lin(ch(4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55;
}

function replaceColors(value: string, map: Record<string, string>): string {
  if (!value) return value;
  return value.replace(COLOR_TOKEN_RE, (match) => {
    const parsed = parseColorToken(match);
    if (!parsed) return match;
    const target = map[parsed.hex];
    if (!target) return match;
    return `#${target}${parsed.alpha ?? ""}`;
  });
}

/** 按 CSS 属性名判断角色（长写属性名） */
export type ColorRole = "text" | "surface" | "border";

const TEXT_PROPS = new Set([
  "color",
  "caret-color",
  "text-decoration-color",
  "text-emphasis-color",
  "-webkit-text-fill-color",
  "fill",
  "stroke",
]);

export function roleOfProperty(prop: string): ColorRole | null {
  const p = prop.toLowerCase();
  if (TEXT_PROPS.has(p)) return "text";
  if (p === "background" || p === "background-color") return "surface";
  if (p === "outline" || p === "outline-color") return "border";
  if (p === "border" || p === "border-color") return "border";
  if (/^border-(top|right|bottom|left)(-color)?$/.test(p)) return "border";
  return null;
}

/**
 * 把一条内联样式值按角色做深色→浅色 / 浅色→深色映射。
 * 值里可以混着别的内容（"1px solid rgb(51, 65, 85)"），只替换颜色记号。
 * @param value 原始值（作者写的深色值，可能是 hex 也可能是 rgb()）
 * @param role  属性角色
 * @param toLight true = 深色→浅色；false = 浅色→深色（反向还原）
 */
export function mapStyleValue(value: string, role: ColorRole, toLight: boolean): string {
  const forward =
    role === "text" ? TEXT_MAP : role === "surface" ? SURFACE_MAP : BORDER_MAP;
  if (toLight) return replaceColors(value, forward);
  // 反向：从浅色值还原成深色值
  const reverse: Record<string, string> = {};
  for (const [from, to] of Object.entries(forward)) reverse[to] = from;
  return replaceColors(value, reverse);
}

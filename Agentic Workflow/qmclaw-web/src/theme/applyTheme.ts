/**
 * applyTheme —— 运行时把旧组件的 inline style 颜色重映射成当前主题
 *
 * 为什么需要它：QMClaw 前端 60+ 个组件把颜色直接写死在 `style={{...}}` 里，
 * 没有 CSS Module，CSS 变量与媒体查询都压不住 inline style。
 * 所以只能在运行时按「CSS 属性角色」把颜色值换掉：
 *   color            → text    表
 *   background*      → surface 表
 *   border* / outline* → border 表
 *
 * 浏览器行为坑（实测结论，本文件的实现都围绕它展开）：
 *   1. CSSOM 会把 inline style 里的颜色**规范化成 rgb()/rgba()**。
 *      `el.style.background = "#0f172a"` 之后
 *      `getPropertyValue("background-color")` 返回 "rgb(15, 23, 42)"。
 *      → 判断「这个值里有没有颜色」不能用 `indexOf("#")`，要用 normalizeColorValue。
 *   2. `style.item(i)` 返回的是**长写属性名**，一个声明会展开成多项：
 *      background → background-image/background-position-x/.../background-color。
 *      → 角色判定要能识别长写名（border-top-color、background-color 等）。
 *   3. 写回 hex 之后 CSSOM 又会把它规范化成 rgb()，
 *      → 比较「当前值」与「上次写入值」必须先归一化，否则会无限重写。
 *
 * 关键点：
 *   - 每个元素的每个属性都记住「作者原值」（归一化成 hex 后的形式），
 *     主题来回切换时从原值重算，而不是反向查表
 *     （反向表会有歧义，例如 #ffffff 既是浅色也是按钮文字色）。
 *   - React 重渲染会改写内联样式，用 MutationObserver 侦测；
 *     值与我们上次写入的不一致 = React 覆盖了 → 重新记录为「作者原值」再映射。
 *   - 归一化后写入的是 hex 形式（如 "#f1f5f9"），CSSOM 读回仍是 rgb()，
 *     但归一化后两者相等，所以不会来回抖动。
 *   - 浅色主题下白色文字（#fff）落在被「刷白」的容器上会看不见，
 *     所以文字角色映射后要额外做一次对比度兜底（见 resolveTextColor）。
 */

import {
  firstColorHex,
  isLightColor,
  mapStyleValue,
  normalizeColorValue,
  roleOfProperty,
  type ColorRole,
} from "./legacyColorMap";

export type ThemeName = "dark" | "light";

/** 元素 → 属性名 → 作者原值（归一化后的字符串，颜色为小写 hex） */
const authored = new WeakMap<HTMLElement, Map<string, string>>();
/** 元素 → 属性名 → 我们上次写入的值（归一化后） */
const lastWritten = new WeakMap<HTMLElement, Map<string, string>>();

let currentTheme: ThemeName = "dark";
let scheduled = false;
let observer: MutationObserver | null = null;

/** 浅色主题下，底色变浅后兜底用的深色文字 */
const FALLBACK_TEXT = "#0f172a";

function themed(authoredValue: string, role: ColorRole, theme: ThemeName): string {
  // 深色主题 = 作者原值；浅色主题 = 按角色映射
  if (theme === "light") return mapStyleValue(authoredValue, role, true);
  return authoredValue;
}

function ensureMap<T>(map: WeakMap<HTMLElement, Map<string, T>>, el: HTMLElement): Map<string, T> {
  let m = map.get(el);
  if (!m) {
    m = new Map();
    map.set(el, m);
  }
  return m;
}

/** 该元素所在位置的实际底色（从元素自身往上找第一个不透明的背景） */
function effectiveBackgroundIsLight(el: HTMLElement): boolean {
  if (typeof window === "undefined" || typeof getComputedStyle !== "function") return false;
  let node: HTMLElement | null = el;
  while (node && node.nodeType === 1) {
    let bg = "";
    try {
      bg = getComputedStyle(node).backgroundColor || "";
    } catch {
      bg = "";
    }
    if (bg && bg !== "transparent" && bg !== "rgba(0, 0, 0, 0)") {
      const hex = firstColorHex(bg);
      if (hex) return isLightColor(hex);
      return false;
    }
    node = node.parentElement;
  }
  return false;
}

/**
 * 文字角色映射后的兜底：
 * 浅色主题下，容器底色被刷白（例如 #1e293b → #ffffff），
 * 而文字仍是浅色（#fff / 未被映射的浅色）就会看不见 → 换成深色字。
 */
function resolveTextColor(mapped: string, el: HTMLElement, theme: ThemeName): string {
  if (theme !== "light") return mapped;
  const hex = firstColorHex(mapped);
  if (!hex || !isLightColor(hex)) return mapped;
  if (!effectiveBackgroundIsLight(el)) return mapped;
  return FALLBACK_TEXT;
}

/** 处理单个元素的内联样式 */
function processElement(el: HTMLElement, theme: ThemeName): void {
  if (!el || el.classList.contains("qm-theme-fixed")) return;
  const style = el.style;
  if (!style || style.length === 0) return;

  const authoredMap = ensureMap(authored, el);
  const writtenMap = ensureMap(lastWritten, el);

  const props: string[] = [];
  for (let i = 0; i < style.length; i++) props.push(style.item(i));

  // 先处理底色/描边，再处理文字：文字的对比度兜底依赖已刷新的底色
  const ordered = [
    ...props.filter((p) => roleOfProperty(p) === "surface" || roleOfProperty(p) === "border"),
    ...props.filter((p) => roleOfProperty(p) === "text"),
  ];

  for (const prop of ordered) {
    const role = roleOfProperty(prop);
    if (!role) continue;
    const raw = style.getPropertyValue(prop);
    if (!raw) continue;
    // 没有颜色记号（如 background-image: none、border-top-width: 1px）→ 跳过
    const norm = normalizeColorValue(raw);
    if (!norm) continue;

    const prevWritten = writtenMap.get(prop);
    let base: string;
    if (prevWritten !== undefined && prevWritten === norm && authoredMap.has(prop)) {
      // 还是我们上次写进去的值 → 作者原值不变
      base = authoredMap.get(prop) as string;
    } else {
      // React 覆盖了这个属性 → 当前值即新的作者原值
      base = norm;
      authoredMap.set(prop, norm);
    }

    let next = themed(base, role, theme);
    if (role === "text") next = resolveTextColor(next, el, theme);
    const nextNorm = normalizeColorValue(next) ?? next;
    writtenMap.set(prop, nextNorm);
    if (nextNorm !== norm) {
      style.setProperty(prop, next);
    }
  }
}

function processTree(root: ParentNode, theme: ThemeName): void {
  // 根先处理：子元素的对比度兜底要看祖先的底色
  if (root instanceof HTMLElement) processElement(root, theme);
  const elements = root.querySelectorAll<HTMLElement>("*");
  for (const el of Array.from(elements)) processElement(el, theme);
}

let applyInProgress = false;

/** 全量应用一次主题 */
export function applyTheme(theme: ThemeName, root?: ParentNode): void {
  currentTheme = theme;
  const target = root ?? (typeof document !== "undefined" ? document.body : null);
  if (!target) return;
  applyInProgress = true;
  try {
    processTree(target, theme);
  } finally {
    applyInProgress = false;
  }
}

/** 待增量处理的元素（MutationObserver 收集） */
const pending = new Set<HTMLElement>();

function flushPending(): void {
  scheduled = false;
  if (applyInProgress) return;
  const list = Array.from(pending);
  pending.clear();
  for (const el of list) {
    if (!el.isConnected) continue;
    processElement(el, currentTheme);
    // 新插入的子树里可能也带内联颜色
    const children = el.querySelectorAll<HTMLElement>("*");
    for (const child of Array.from(children)) processElement(child, currentTheme);
  }
}

function scheduleApply(target?: HTMLElement): void {
  if (target) pending.add(target);
  if (scheduled) return;
  scheduled = true;
  setTimeout(flushPending, 120);
}

/** 挂上观察器：React 重渲染改了内联样式就重新映射 */
export function startThemeObserver(): () => void {
  if (typeof document === "undefined") return () => {};
  if (observer) return () => {};
  observer = new MutationObserver((records) => {
    if (applyInProgress) return;
    for (const rec of records) {
      if (rec.type === "childList") {
        rec.addedNodes.forEach((node) => {
          if (node instanceof HTMLElement) pending.add(node);
        });
      } else if (rec.type === "attributes" && rec.target instanceof HTMLElement) {
        pending.add(rec.target);
      }
    }
    scheduleApply();
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["style"],
  });
  return () => {
    observer?.disconnect();
    observer = null;
  };
}

/** 供页面在需要时主动重刷（例如组件大改后） */
export function refreshTheme(): void {
  applyTheme(currentTheme);
}

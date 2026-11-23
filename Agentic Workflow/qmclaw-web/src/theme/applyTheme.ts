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
 * 关键点：
 *   - 每个元素的每个属性都记住「作者原值」，主题来回切换时从原值重算，
 *     而不是反向查表（反向表会有歧义，例如 #ffffff 既是浅色也是按钮文字色）。
 *   - React 重渲染会改写内联样式，用 MutationObserver 侦测；
 *     值与我们上次写入的不一致 = React 覆盖了 → 重新记录为「作者原值」再映射。
 */

import { mapStyleValue, roleOfProperty, type ColorRole } from "./legacyColorMap";

export type ThemeName = "dark" | "light";

/** 元素 → 属性名 → { authored: 作者写的原值, role: 角色 } */
const authored = new WeakMap<HTMLElement, Map<string, { authored: string; role: ColorRole }>>();
/** 元素 → 属性名 → 我们上次写入的值（用于识别「是我们写的」还是「React 覆盖的」） */
const lastWritten = new WeakMap<HTMLElement, Map<string, string>>();

let currentTheme: ThemeName = "dark";
let scheduled = false;
let observer: MutationObserver | null = null;

function themed(authoredValue: string, role: ColorRole, theme: ThemeName): string {
  // 深色主题 = 作者原值；浅色主题 = 按角色映射
  if (theme === "light") return mapStyleValue(authoredValue, role, true);
  return authoredValue;
}

function readMap<T>(map: WeakMap<HTMLElement, Map<string, T>>, el: HTMLElement): Map<string, T> | undefined {
  return map.get(el);
}

/** 处理单个元素的内联样式 */
function processElement(el: HTMLElement, theme: ThemeName): void {
  if (el.classList.contains("qm-theme-fixed")) return;
  const style = el.style;
  if (!style || style.length === 0) return;

  let authoredMap = readMap(authored, el);
  if (!authoredMap) {
    authoredMap = new Map();
    authored.set(el, authoredMap);
  }
  let writtenMap = readMap(lastWritten, el);
  if (!writtenMap) {
    writtenMap = new Map();
    lastWritten.set(el, writtenMap);
  }

  const props: string[] = [];
  for (let i = 0; i < style.length; i++) props.push(style.item(i));

  for (const prop of props) {
    const role = roleOfProperty(prop);
    if (!role) continue;
    const value = style.getPropertyValue(prop);
    if (!value || value.indexOf("#") === -1) continue;

    const prevWritten = writtenMap.get(prop);
    const prevAuthored = authoredMap.get(prop);
    let base: string;
    if (prevAuthored && prevWritten !== undefined && value === prevWritten) {
      // 还是我们上次写进去的值 → 作者原值不变
      base = prevAuthored.authored;
    } else {
      // React 覆盖了这个属性 → 当前值即新的作者原值
      base = value;
      authoredMap.set(prop, { authored: base, role });
    }

    const next = themed(base, role, theme);
    if (next !== value) {
      style.setProperty(prop, next);
      writtenMap.set(prop, next);
    } else {
      writtenMap.set(prop, next);
    }
  }
}

function applyToTree(root: ParentNode, theme: ThemeName): void {
  const elements = root.querySelectorAll<HTMLElement>("*");
  for (const el of Array.from(elements)) processElement(el, theme);
  if (root instanceof HTMLElement) processElement(root, theme);
}

let applyInProgress = false;

/** 全量应用一次主题 */
export function applyTheme(theme: ThemeName, root?: ParentNode): void {
  currentTheme = theme;
  const target = root ?? (typeof document !== "undefined" ? document.body : null);
  if (!target) return;
  applyInProgress = true;
  try {
    applyToTree(target, theme);
  } finally {
    applyInProgress = false;
  }
}

function scheduleApply(): void {
  if (scheduled) return;
  scheduled = true;
  setTimeout(() => {
    scheduled = false;
    if (applyInProgress) return;
    applyTheme(currentTheme);
  }, 120);
}

/** 挂上观察器：React 重渲染改了内联样式就重新映射 */
export function startThemeObserver(): () => void {
  if (typeof document === "undefined") return () => {};
  if (observer) return () => {};
  observer = new MutationObserver(() => {
    if (applyInProgress) return;
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

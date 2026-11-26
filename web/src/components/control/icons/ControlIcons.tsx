/**
 * /control 控制台的图标组。
 *
 * 图形来自 guizang-social-card-skill 生成的 QMClaw 方形卡片（瑞士风 IKB），
 * 这里把同一套几何语言缩到界面尺寸：纯方块、连线、三角箭头，
 * 线宽按小尺寸加粗，accent 用 --qm-accent，墨色用 currentColor（跟随主题）。
 *
 * 约定：所有图标都是 400×400 视框、方形、无圆角，跟卡片的直角语言一致。
 */

export interface ControlIconProps {
  /** 像素尺寸，默认 24 */
  size?: number;
  className?: string;
}

const ACCENT = "var(--qm-accent)";

/** 01 项目主标：外框 + 实心方块 + 三道爪痕 */
export function IconQmclawMark({ size = 32, className }: ControlIconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 400 400"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="26" y="26" width="348" height="348" fill="none" stroke="currentColor" strokeWidth="26" />
      <rect x="170" y="72" width="150" height="150" fill={ACCENT} />
      <g stroke={ACCENT} strokeWidth="26" strokeLinecap="butt">
        <line x1="70" y1="330" x2="150" y2="250" />
        <line x1="120" y1="330" x2="200" y2="250" />
        <line x1="170" y1="330" x2="250" y2="250" />
      </g>
    </svg>
  );
}

/** 02 测控状态机：两格空心 + 中间 accent 格 + 迁移箭头 */
export function IconStateMachine({ size = 24, className }: ControlIconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 400 400"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="18" y="150" width="104" height="104" fill="none" stroke="currentColor" strokeWidth="26" />
      <rect x="148" y="150" width="104" height="104" fill={ACCENT} />
      <rect x="278" y="150" width="104" height="104" fill="none" stroke="currentColor" strokeWidth="26" />
      <g fill="currentColor">
        <polygon points="128,180 156,202 128,224" />
        <polygon points="258,180 286,202 258,224" />
      </g>
    </svg>
  );
}

/** 03 QWF 工作流：一支 accent 起点、两个空心节点、带箭头的连线 */
export function IconQwfFlow({ size = 24, className }: ControlIconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 400 400"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="34" y="34" width="120" height="120" fill={ACCENT} />
      <rect x="246" y="34" width="120" height="120" fill="none" stroke="currentColor" strokeWidth="24" />
      <rect x="140" y="246" width="120" height="120" fill="none" stroke="currentColor" strokeWidth="24" />
      <g stroke="currentColor" strokeWidth="20" fill="none">
        <line x1="162" y1="94" x2="232" y2="94" />
        <path d="M94 164 V206 H200 V234" />
      </g>
      <g fill="currentColor">
        <polygon points="232,74 258,94 232,114" />
        <polygon points="180,234 200,260 220,234" />
      </g>
    </svg>
  );
}

/** 04 实验动态：阶梯折线 + 基线 + 箭头 */
export function IconLiveLab({ size = 24, className }: ControlIconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 400 400"
      aria-hidden="true"
      focusable="false"
    >
      <line x1="26" y1="344" x2="374" y2="344" stroke="currentColor" strokeWidth="18" />
      <polyline
        points="40,284 130,284 130,176 220,176 220,254 300,254 300,124 356,124"
        fill="none"
        stroke={ACCENT}
        strokeWidth="26"
        strokeLinejoin="miter"
      />
      <polygon points="352,100 384,124 352,148" fill={ACCENT} />
    </svg>
  );
}

/** 05 记忆与智能体：中心 accent 格 + 四角节点 + 连线 */
export function IconMemoryAgent({ size = 24, className }: ControlIconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 400 400"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="160" y="160" width="80" height="80" fill={ACCENT} />
      <g fill="none" stroke="currentColor" strokeWidth="24">
        <rect x="26" y="26" width="86" height="86" />
        <rect x="288" y="26" width="86" height="86" />
        <rect x="26" y="288" width="86" height="86" />
        <rect x="288" y="288" width="86" height="86" />
      </g>
      <g stroke="currentColor" strokeWidth="18" fill="none">
        <line x1="160" y1="160" x2="112" y2="112" />
        <line x1="240" y1="160" x2="288" y2="112" />
        <line x1="160" y1="240" x2="112" y2="288" />
        <line x1="240" y1="240" x2="288" y2="288" />
      </g>
    </svg>
  );
}

/** 06 运行记录：三行台账 + 左侧状态块（按同一语言补的一个图标） */
export function IconRunHistory({ size = 24, className }: ControlIconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 400 400"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="22" y="86" width="30" height="48" fill={ACCENT} />
      <rect x="22" y="176" width="30" height="48" fill="currentColor" />
      <rect x="22" y="266" width="30" height="48" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="26" strokeLinecap="butt">
        <line x1="86" y1="110" x2="352" y2="110" />
        <line x1="86" y1="200" x2="352" y2="200" />
        <line x1="86" y1="290" x2="252" y2="290" />
      </g>
    </svg>
  );
}

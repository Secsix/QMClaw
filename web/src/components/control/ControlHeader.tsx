"use client";

import ThemeToggle from "@/components/ThemeToggle";

interface Props {
  qubits: string[];
  selectedQubit: string;
  onSelectQubit: (name: string) => void;
  onRefresh: () => void;
  status: string;
  lastUpdated: number | null;
  error: string | null;
  /** 部分端点失败（页面仍可用），提示但不遮蔽状态。 */
  partialErrors?: string[];
}

/**
 * 控制台顶栏（瑞士风）。
 *
 * 信息分三层：品牌块（识别）→ 状态元信息（这一轮的数据新鲜度）→ 操作区。
 * 状态用「方点 + 大写标签」的药丸，一眼能区分 running / idle / 后端异常。
 */
export default function ControlHeader({
  qubits,
  selectedQubit,
  onSelectQubit,
  onRefresh,
  status,
  lastUpdated,
  error,
  partialErrors = [],
}: Props) {
  const state = error ? "error" : status === "running" ? "running" : "idle";
  const stateColor =
    state === "error"
      ? "var(--qm-err)"
      : state === "running"
        ? "var(--qm-accent)"
        : "var(--qm-ok)";
  const stateLabel = error ? "后端异常" : status;

  return (
    <header className="qm-control-header">
      <div className="qm-brand">
        <span className="qm-brand-mark" aria-hidden="true">
          QC
        </span>
        <div className="qm-brand-text">
          <span className="qm-kicker">Quantum Measurement &amp; Control</span>
          <h1 className="qm-brand-title">qmclaw · 测控控制台</h1>
        </div>
      </div>

      <div className="qm-header-meta">
        <span className="qm-status-pill" data-state={state} style={{ color: stateColor }}>
          <span className="qm-status-dot" />
          {stateLabel}
        </span>

        {lastUpdated ? (
          <span className="qm-meta-text">
            更新 {new Date(lastUpdated).toLocaleTimeString("zh-CN", { hour12: false })}
          </span>
        ) : null}

        {partialErrors.length > 0 ? (
          <span className="qm-degrade-chip" title={partialErrors.join("\n")}>
            部分降级 · {partialErrors.length}
          </span>
        ) : null}
      </div>

      <div className="qm-header-actions">
        {qubits.length > 0 ? (
          <label className="qm-field">
            <span className="qm-field-label">Qubit</span>
            <select
              className="qm-select"
              value={selectedQubit}
              onChange={(e) => onSelectQubit(e.target.value)}
            >
              {qubits.map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <button type="button" className="qm-btn" onClick={onRefresh} title="立即刷新">
          ⟳ 刷新
        </button>

        <ThemeToggle compact />

        <a className="qm-btn" href="/">
          工作台 →
        </a>
      </div>
    </header>
  );
}

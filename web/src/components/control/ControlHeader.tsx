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
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.6rem",
        flexWrap: "wrap",
        padding: "0.5rem 0.8rem",
        borderBottom: "1px solid var(--qm-border)",
        background: "var(--qm-panel)",
        flexShrink: 0,
      }}
    >
      <span style={{ fontSize: "1rem", fontWeight: 700, color: "var(--qm-text)" }}>
        ⚛ qmclaw · 测控控制台
      </span>

      <span
        style={{
          fontSize: "0.72rem",
          padding: "0.1rem 0.4rem",
          borderRadius: "0.25rem",
          border: `1px solid ${
            error
              ? "var(--qm-err)"
              : status === "running"
                ? "var(--qm-accent)"
                : "var(--qm-ok)"
          }`,
          color: error
            ? "var(--qm-err)"
            : status === "running"
              ? "var(--qm-accent)"
              : "var(--qm-ok)",
        }}
      >
        {error ? "后端异常" : status}
      </span>

      {lastUpdated ? (
        <span style={{ fontSize: "0.7rem", color: "var(--qm-text-mute)" }}>
          更新于 {new Date(lastUpdated).toLocaleTimeString("zh-CN", { hour12: false })}
        </span>
      ) : null}

      {partialErrors.length > 0 ? (
        <span
          title={partialErrors.join("\n")}
          style={{
            fontSize: "0.7rem",
            color: "var(--qm-warn)",
            border: "1px solid var(--qm-warn)",
            borderRadius: "0.25rem",
            padding: "0.05rem 0.3rem",
            cursor: "help",
          }}
        >
          部分降级（{partialErrors.length}）
        </span>
      ) : null}

      <div
        style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
          flexWrap: "wrap",
        }}
      >
        {qubits.length > 0 && (
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.3rem",
              fontSize: "0.75rem",
              color: "var(--qm-text-dim)",
            }}
          >
            比特
            <select
              value={selectedQubit}
              onChange={(e) => onSelectQubit(e.target.value)}
              style={{
                background: "var(--qm-bg-2)",
                color: "var(--qm-text)",
                border: "1px solid var(--qm-border)",
                borderRadius: "0.3rem",
                padding: "0.2rem 0.35rem",
                fontSize: "0.75rem",
              }}
            >
              {qubits.map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
            </select>
          </label>
        )}

        <button
          onClick={onRefresh}
          title="立即刷新"
          style={{
            background: "var(--qm-panel-2)",
            color: "var(--qm-text)",
            border: "1px solid var(--qm-border)",
            borderRadius: "0.3rem",
            padding: "0.22rem 0.5rem",
            fontSize: "0.75rem",
            cursor: "pointer",
          }}
        >
          ⟳ 刷新
        </button>

        <ThemeToggle compact />

        <a
          href="/"
          style={{
            fontSize: "0.72rem",
            color: "var(--qm-text-dim)",
            textDecoration: "none",
            border: "1px solid var(--qm-border)",
            borderRadius: "0.3rem",
            padding: "0.2rem 0.45rem",
          }}
        >
          工作台 →
        </a>
      </div>
    </header>
  );
}

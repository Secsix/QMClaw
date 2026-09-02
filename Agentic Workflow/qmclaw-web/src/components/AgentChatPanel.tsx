"use client";

/**
 * Quantum Control Agent Chat Panel
 * Natural language interface for quantum measurement and control tasks
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../lib/api";
import { useModelStore } from "../store/modelStore";

interface AgentStep {
  thought?: string;
  tool: string;
  input: Record<string, unknown>;
  observation?: string;
  reflection?: string;
  retried?: boolean;
}

interface AgentResult {
  response?: string;
  steps?: AgentStep[];
  results?: Record<string, number>;
  charts?: string[];
  error?: string;
}

interface ChatMessage {
  role: "user" | "agent";
  content: string;
  result?: AgentResult;
}

const AGENT_MODES = [
  { value: "react", label: "ReAct", desc: "Think-Act-Observe 循环" },
  { value: "plan_and_execute", label: "Plan & Execute", desc: "先规划后执行" },
  { value: "reflexion", label: "Reflexion", desc: "执行后反思纠错" },
];

export default function AgentChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("react");
  const [running, setRunning] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const models = useModelStore((s) => s.models);
  const fetchModels = useModelStore((s) => s.fetchModels);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  const textModels = models.filter((m) => m.enabled && m.capabilities.includes("text"));

  useEffect(() => {
    if (textModels.length > 0 && !selectedModel) {
      setSelectedModel(textModels[0].id);
    }
  }, [textModels, selectedModel]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = useCallback(async () => {
    if (!input.trim() || running) return;
    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setRunning(true);

    try {
      const result = await api.agentChat(
        userMsg,
        mode,
        { model_id: selectedModel }
      ) as AgentResult & { error?: string };

      if (result.error) {
        setMessages((prev) => [
          ...prev,
          { role: "agent", content: `错误: ${result.error}`, result: {} },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: "agent", content: result.response || "执行完成", result },
        ]);
      }
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        { role: "agent", content: `请求失败: ${e.message}`, result: {} },
      ]);
    } finally {
      setRunning(false);
    }
  }, [input, mode, selectedModel, running]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
      {/* Header */}
      <div style={{
        background: "#1e293b",
        borderRadius: "8px",
        padding: "12px 16px",
        border: "1px solid #334155",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        flexWrap: "wrap",
      }}>
        <span style={{ fontSize: "14px", fontWeight: 700, color: "#e2e8f0" }}>🤖 量子测控智能体</span>

        {/* Mode selector */}
        <div style={{ display: "flex", gap: "6px" }}>
          {AGENT_MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              title={m.desc}
              style={{
                padding: "4px 10px",
                borderRadius: "6px",
                border: "1px solid",
                borderColor: mode === m.value ? "#38bdf8" : "#334155",
                background: mode === m.value ? "#1e3a5f" : "#0f172a",
                color: mode === m.value ? "#38bdf8" : "#64748b",
                fontSize: "11px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {m.label}
            </button>
          ))}
        </div>

        {/* Model selector */}
        <select
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          style={{
            padding: "4px 10px",
            background: "#0f172a",
            border: "1px solid #334155",
            borderRadius: "6px",
            color: "#e2e8f0",
            fontSize: "11px",
            marginLeft: "auto",
          }}
        >
          {textModels.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </div>

      {/* Chat area */}
      <div style={{
        flex: 1,
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        padding: "4px",
      }}>
        {messages.length === 0 && (
          <div style={{
            textAlign: "center",
            color: "#475569",
            fontSize: "13px",
            marginTop: "40px",
          }}>
            <div style={{ fontSize: "32px", marginBottom: "8px" }}>🔬</div>
            <div>输入自然语言指令开始测控任务</div>
            <div style={{ fontSize: "11px", marginTop: "8px", color: "#334155" }}>
              示例：「对 q10lu1 做 T1 实验并分析结果」
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i}>
            {msg.role === "user" ? (
              /* User bubble */
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <div style={{
                  maxWidth: "75%",
                  padding: "10px 14px",
                  background: "#0369a1",
                  borderRadius: "12px 12px 4px 12px",
                  color: "#e2e8f0",
                  fontSize: "13px",
                }}>
                  {msg.content}
                </div>
              </div>
            ) : (
              /* Agent bubble + result */
              <div>
                <div style={{
                  padding: "10px 14px",
                  background: "#1e293b",
                  borderRadius: "12px 12px 12px 4px",
                  color: "#e2e8f0",
                  fontSize: "13px",
                  border: "1px solid #334155",
                }}>
                  {msg.content}
                </div>

                {/* Execution steps */}
                {msg.result?.steps && msg.result.steps.length > 0 && (
                  <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
                    {msg.result.steps.map((step, si) => (
                      <div key={si} style={{
                        background: "#0f172a",
                        border: "1px solid #1e293b",
                        borderRadius: "8px",
                        overflow: "hidden",
                        fontSize: "11px",
                      }}>
                        <div style={{
                          padding: "6px 10px",
                          background: "#1e293b",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                          borderBottom: "1px solid #0f172a",
                        }}>
                          <span style={{ color: "#38bdf8", fontWeight: 700 }}>[Step {si + 1}]</span>
                          <span style={{
                            padding: "1px 6px",
                            background: "#7c3aed",
                            borderRadius: "4px",
                            color: "#e2e8f0",
                            fontSize: "10px",
                            fontFamily: "monospace",
                          }}>
                            {step.tool}
                          </span>
                          {step.retried && (
                            <span style={{ color: "#f59e0b", fontSize: "10px" }}>↻ 重试</span>
                          )}
                        </div>
                        <div style={{ padding: "6px 10px" }}>
                          {step.thought && (
                            <div style={{ color: "#94a3b8", marginBottom: "4px" }}>
                              💭 {step.thought}
                            </div>
                          )}
                          <div style={{ color: "#64748b", fontFamily: "monospace", fontSize: "10px" }}>
                            Input: {JSON.stringify(step.input)}
                          </div>
                          {step.observation && (
                            <div style={{ color: "#22c55e", marginTop: "4px", fontFamily: "monospace" }}>
                              → {typeof step.observation === "object"
                                ? JSON.stringify(step.observation)
                                : String(step.observation)}
                            </div>
                          )}
                          {step.reflection && (
                            <div style={{ color: "#f59e0b", marginTop: "4px", fontSize: "10px" }}>
                              🤔 反思: {step.reflection.slice(0, 100)}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Results card */}
                {msg.result?.results && Object.keys(msg.result.results).length > 0 && (
                  <div style={{
                    marginTop: "8px",
                    background: "#0f172a",
                    border: "1px solid #22c55e40",
                    borderRadius: "8px",
                    padding: "10px 14px",
                  }}>
                    <div style={{ fontSize: "11px", color: "#22c55e", fontWeight: 700, marginBottom: "6px" }}>
                      📊 执行结果
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: "6px" }}>
                      {Object.entries(msg.result.results).map(([k, v]) => (
                        <div key={k} style={{
                          background: "#1e293b",
                          borderRadius: "6px",
                          padding: "6px 10px",
                          textAlign: "center",
                        }}>
                          <div style={{ color: "#64748b", fontSize: "10px" }}>{k}</div>
                          <div style={{ color: "#22c55e", fontSize: "13px", fontWeight: 700, fontFamily: "monospace" }}>
                            {typeof v === "number" ? v.toFixed(4) : v}
                          </div>
                        </div>
                      ))}
                    </div>
                    {msg.result.charts && msg.result.charts.length > 0 && (
                      <div style={{ marginTop: "8px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
                        {msg.result.charts.map((chart, ci) => (
                          <div key={ci} style={{
                            padding: "4px 8px",
                            background: "#1e293b",
                            borderRadius: "4px",
                            fontSize: "10px",
                            color: "#38bdf8",
                          }}>
                            📈 {chart.split(/[/\\]/).pop()}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {running && (
          <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "#38bdf8", fontSize: "13px" }}>
            <div style={{
              width: "8px", height: "8px",
              background: "#38bdf8", borderRadius: "50%",
              animation: "pulse 1s infinite",
            }} />
            智能体思考中...
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div style={{
        background: "#1e293b",
        borderRadius: "8px",
        padding: "12px",
        border: "1px solid #334155",
        display: "flex",
        gap: "8px",
        alignItems: "flex-end",
      }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="下达测控任务指令，如：对 q10lu1 做 T1 实验并分析..."
          disabled={running}
          style={{
            flex: 1,
            background: "#0f172a",
            border: "1px solid #334155",
            borderRadius: "8px",
            padding: "8px 12px",
            color: "#e2e8f0",
            fontSize: "13px",
            resize: "none",
            minHeight: "44px",
            maxHeight: "120px",
            fontFamily: "inherit",
            outline: "none",
          }}
          rows={1}
        />
        <button
          onClick={handleSend}
          disabled={running || !input.trim()}
          style={{
            padding: "8px 16px",
            background: running ? "#1e3a5f" : "#0369a1",
            border: "none",
            borderRadius: "8px",
            color: "#e2e8f0",
            fontSize: "13px",
            fontWeight: 600,
            cursor: running ? "not-allowed" : "pointer",
            opacity: running ? 0.6 : 1,
          }}
        >
          {running ? "..." : "➤"}
        </button>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}

/**
 * Qiskit Backend Panel — 用 qiskit/Aer 在本地模拟跑实验
 *
 * 这是 quantum_service 的**第三个后端**（与 `sq.*` 真机、离线合成并列），
 * 走的是增量端点 `/qiskit/*`，**不改动**现有的 mode(online/offline/auto) 语义 ——
 * 那三态管的是 LabRAD 真机，qiskit 完全不碰 LabRAD。
 *
 * 界面上有三件事必须让用户看见，否则容易误判数据：
 *
 * 1. **qiskit 是可选依赖**：没装时 `health.available=false`，面板照样打得开，
 *    只是跑不了 —— 并且给出明确的安装指引，而不是一个看不懂的报错。
 * 2. **结果是无量纲的**（qiskit 里 ħ=1）：结果区显著标出
 *    `unit_system = qiskit_normalized`，防止把模拟值当成真机标定值。
 * 3. **exact / model 两种近似**：每个实验挂徽标。
 *    `model` 表示这是"等效模型"而非真实的物理过程（比如谱学用的是
 *    Landau-Zener 长脉冲模型），拿去做真机标定会出问题。
 */

"use client";

import { useState, useEffect, useMemo } from 'react';
import { api, normalizePlotUrl } from '../lib/api';

interface Props {
  onClose: () => void;
}

interface ParamSpec {
  default: unknown;
  unit?: string;
  desc?: string;
}

interface ExperimentSpec {
  name: string;
  title: string;
  approximation: 'exact' | 'model';
  description: string;
  params: Record<string, ParamSpec>;
  metrics: string[];
  real_function?: string | null;
  available_on_hardware?: boolean;
  hardware_note?: string | null;
}

interface HealthInfo {
  available?: boolean;
  enabled?: boolean;
  execution?: string;
  versions?: Record<string, string | null>;
  python_executable?: string;
  service_python?: string;
  error?: string | null;
  install_hint?: string;
  unit_system?: string;
  experiments?: number;
  [k: string]: unknown;
}

interface RunResult {
  status?: string;
  experiment?: string;
  qubit?: string;
  backend?: string;
  unit_system?: string;
  approximation?: string;
  stdout?: string;
  stderr?: string;
  error?: string | null;
  metrics?: Record<string, unknown>;
  data?: { type: string; x: number[]; y: number[]; xlabel: string; ylabel: string; title: string };
  plotPath?: string;
  elapsed_ms?: number;
  shots?: number;
  seed?: number;
  [k: string]: unknown;
}

const C = {
  bg: '#0f172a',
  panel: '#1e293b',
  border: '#334155',
  text: '#e2e8f0',
  dim: '#94a3b8',
  accent: '#38bdf8',
  ok: '#22c55e',
  warn: '#f59e0b',
  bad: '#ef4444',
};

/** 结果状态 → 颜色/文案 */
function statusStyle(status: string | undefined): { color: string; label: string } {
  switch (status) {
    case 'qiskit_completed':
      return { color: C.ok, label: '✅ 完成' };
    case 'qiskit_unavailable':
      return { color: C.warn, label: '⚠️ 后端不可用' };
    case 'qiskit_timeout':
      return { color: C.warn, label: '⏱ 超时' };
    case 'qiskit_error':
      return { color: C.bad, label: '❌ 失败' };
    default:
      return { color: C.dim, label: status || '未知' };
  }
}

export default function QiskitBackendPanel({ onClose }: Props) {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [experiments, setExperiments] = useState<ExperimentSpec[]>([]);
  const [coverage, setCoverage] = useState<Record<string, unknown> | null>(null);

  const [selected, setSelected] = useState<string>('t1');
  const [qubit, setQubit] = useState('q0');
  const [shots, setShots] = useState(2000);
  const [seed, setSeed] = useState(42);
  /** 参数值统一存字符串，提交时按 spec 的默认值类型强转 */
  const [paramValues, setParamValues] = useState<Record<string, string>>({});

  const [circuitMode, setCircuitMode] = useState(false);
  const [qasm, setQasm] = useState(
    'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\nmeasure q[0] -> c[0];\nmeasure q[1] -> c[1];\n'
  );

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const spec = useMemo(
    () => experiments.find((e) => e.name === selected) || null,
    [experiments, selected]
  );

  // 目录 + 健康状态：两个都调一次，任何一个失败都不该让面板空白
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const h = await api.qiskitHealth();
        if (alive) setHealth(h);
      } catch (e) {
        if (alive) setHealth({ available: false, error: String(e) });
      }
      try {
        const cat = await api.qiskitExperiments();
        if (!alive) return;
        setExperiments(cat.experiments || []);
        setCoverage(cat.coverage || null);
      } catch (e) {
        if (alive) setError(`实验目录读取失败：${e}`);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 切换实验时用 spec 的默认值预填参数
  useEffect(() => {
    if (!spec) return;
    const next: Record<string, string> = {};
    for (const [key, p] of Object.entries(spec.params || {})) {
      next[key] = p?.default === null || p?.default === undefined ? '' : String(p.default);
    }
    setParamValues(next);
    setResult(null);
    setError(null);
  }, [spec]);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const body: Parameters<typeof api.qiskitRun>[0] = { qubit, shots, seed };
      if (circuitMode) {
        body.circuit = qasm;
      } else {
        body.experiment = selected;
        const params: Record<string, unknown> = {};
        for (const [key, raw] of Object.entries(paramValues)) {
          if (raw === '' || raw === undefined) continue;
          const def = spec?.params?.[key]?.default;
          // 按默认值类型强转：数值型参数不能当字符串发给后端
          const asNumber = typeof def === 'number' ? Number(raw) : NaN;
          params[key] = Number.isFinite(asNumber) ? asNumber : raw;
        }
        body.params = params;
      }
      const res = await api.qiskitRun(body);
      setResult(res as RunResult);
      if (res?.error) setError(String(res.error));
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  };

  const available = health?.available === true;
  const plotUrl = result?.plotPath ? normalizePlotUrl(result.plotPath) : null;

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.7)', display: 'flex',
      alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }}>
      <div style={{
        width: '1080px', maxWidth: '95vw', maxHeight: '90vh',
        background: C.bg, border: `1px solid ${C.border}`, borderRadius: '12px',
        overflow: 'hidden', display: 'flex', flexDirection: 'column',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 20px', background: C.panel, borderBottom: `1px solid ${C.border}`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '20px' }}>⚛️</span>
            <div>
              <div style={{ fontWeight: 600 }}>Qiskit 模拟后端</div>
              <div style={{ fontSize: '11px', color: C.dim }}>
                qiskit/Aer 本地模拟 · 不改动 online/offline/auto 语义 · 无写闸门（无物理副作用）
              </div>
            </div>
          </div>
          <button onClick={onClose} style={{
            background: 'transparent', border: `1px solid ${C.border}`, color: C.text,
            borderRadius: '6px', padding: '4px 10px', cursor: 'pointer',
          }}>关闭</button>
        </div>

        {/* Health banner */}
        <div style={{
          padding: '10px 20px', borderBottom: `1px solid ${C.border}`,
          background: available ? 'rgba(34,197,94,0.08)' : 'rgba(245,158,11,0.08)',
          fontSize: '12px', display: 'flex', gap: '16px', flexWrap: 'wrap', alignItems: 'center',
        }}>
          <span style={{ color: available ? C.ok : C.warn, fontWeight: 600 }}>
            {health === null ? '探测中…' : available ? '后端可用' : '后端不可用'}
          </span>
          {health?.execution && (
            <span style={{ color: C.dim }}>
              执行方式：{health.execution === 'subprocess' ? '子进程（独立环境）' : '进程内'}
            </span>
          )}
          {health?.versions?.qiskit && (
            <span style={{ color: C.dim }}>
              qiskit {health.versions.qiskit} · aer {health.versions.qiskit_aer ?? '—'}
            </span>
          )}
          {health?.experiments !== undefined && (
            <span style={{ color: C.dim }}>实验数 {health.experiments}</span>
          )}
          {health?.unit_system && (
            <span style={{ color: C.accent }} title="qiskit 里 ħ=1，时间/频率无量纲，靠入参先验换算">
              单位制 {health.unit_system}
            </span>
          )}
          {!available && health?.error && (
            <span style={{ color: C.warn }}>原因：{health.error}</span>
          )}
          {!available && health?.install_hint && (
            <span style={{ color: C.dim }}>装法：{health.install_hint}</span>
          )}
        </div>

        {/* Body: 左目录 / 右表单+结果 */}
        <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', flex: 1, minHeight: 0 }}>

          {/* 实验目录 */}
          <div style={{ borderRight: `1px solid ${C.border}`, overflowY: 'auto', padding: '10px' }}>
            <div style={{ fontSize: '11px', color: C.dim, marginBottom: '8px', padding: '0 4px' }}>
              实验目录（与真机词表同名）
            </div>
            {experiments.length === 0 && (
              <div style={{ fontSize: '12px', color: C.dim, padding: '4px' }}>加载中…</div>
            )}
            {experiments.map((e) => {
              const active = !circuitMode && e.name === selected;
              return (
                <button
                  key={e.name}
                  onClick={() => { setCircuitMode(false); setSelected(e.name); }}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left',
                    padding: '7px 8px', marginBottom: '4px', borderRadius: '6px',
                    border: `1px solid ${active ? C.accent : 'transparent'}`,
                    background: active ? 'rgba(56,189,248,0.12)' : 'transparent',
                    color: C.text, cursor: 'pointer',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '12px', fontWeight: 600 }}>{e.name}</span>
                    <span style={{
                      fontSize: '9px', padding: '1px 5px', borderRadius: '999px',
                      background: e.approximation === 'exact' ? 'rgba(34,197,94,0.18)' : 'rgba(245,158,11,0.18)',
                      color: e.approximation === 'exact' ? C.ok : C.warn,
                    }}>
                      {e.approximation}
                    </span>
                  </div>
                  <div style={{ fontSize: '10px', color: C.dim, marginTop: '2px' }}>
                    {e.metrics.join(', ') || '—'}
                  </div>
                </button>
              );
            })}

            <div style={{ marginTop: '10px', borderTop: `1px solid ${C.border}`, paddingTop: '8px' }}>
              <button
                onClick={() => { setCircuitMode(true); setResult(null); setError(null); }}
                style={{
                  display: 'block', width: '100%', textAlign: 'left',
                  padding: '7px 8px', borderRadius: '6px',
                  border: `1px solid ${circuitMode ? C.accent : 'transparent'}`,
                  background: circuitMode ? 'rgba(56,189,248,0.12)' : 'transparent',
                  color: C.text, cursor: 'pointer', fontSize: '12px',
                }}
              >
                <span style={{ fontWeight: 600 }}>自定义线路</span>
                <div style={{ fontSize: '10px', color: C.dim, marginTop: '2px' }}>QASM2 → counts</div>
              </button>
            </div>

            {coverage && (
              <div style={{ marginTop: '12px', fontSize: '10px', color: C.dim, lineHeight: 1.5 }}>
                覆盖 {String(coverage.covered)}/{String(coverage.total)} 个真机实验
                {Array.isArray(coverage.missing_in_qiskit) && coverage.missing_in_qiskit.length > 0 && (
                  <div style={{ color: C.warn }}>
                    缺：{(coverage.missing_in_qiskit as string[]).join(', ')}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 右侧 */}
          <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div style={{ padding: '12px 16px', overflowY: 'auto', borderBottom: `1px solid ${C.border}` }}>
              {!circuitMode && spec && (
                <>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '4px' }}>
                    <span style={{ fontWeight: 600 }}>{spec.title}</span>
                    <span style={{ fontSize: '11px', color: C.dim }}>{spec.name}</span>
                    {spec.available_on_hardware === false && (
                      <span
                        title={spec.hardware_note || '真机侧没有对应函数'}
                        style={{ fontSize: '10px', color: C.warn, border: `1px solid ${C.warn}`, borderRadius: '4px', padding: '0 4px' }}
                      >
                        真机不可用
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: '11px', color: C.dim, marginBottom: '10px' }}>
                    {spec.description}
                  </div>
                </>
              )}

              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
                {!circuitMode && (
                  <label style={{ fontSize: '11px', color: C.dim }}>
                    比特
                    <input value={qubit} onChange={(e) => setQubit(e.target.value)} style={inputStyle} />
                  </label>
                )}
                <label style={{ fontSize: '11px', color: C.dim }}>
                  shots
                  <input
                    type="number" value={shots} min={1}
                    onChange={(e) => setShots(Number(e.target.value))} style={inputStyle}
                  />
                </label>
                <label style={{ fontSize: '11px', color: C.dim }}>
                  seed
                  <input
                    type="number" value={seed} min={0}
                    onChange={(e) => setSeed(Number(e.target.value))} style={inputStyle}
                  />
                </label>
                <button
                  onClick={run}
                  disabled={running || !available}
                  title={available ? '' : '后端不可用，先按提示安装 qiskit'}
                  style={{
                    padding: '7px 18px', borderRadius: '6px', border: 'none',
                    background: running || !available ? '#475569' : C.accent,
                    color: '#0f172a', fontWeight: 700,
                    cursor: running || !available ? 'not-allowed' : 'pointer',
                  }}
                >
                  {running ? '执行中…' : '运行'}
                </button>
              </div>

              {/* 参数表 */}
              {!circuitMode && spec && Object.keys(spec.params || {}).length > 0 && (
                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginTop: '10px' }}>
                  {Object.entries(spec.params).map(([key, p]) => (
                    <label
                      key={key}
                      title={p?.desc || ''}
                      style={{ fontSize: '11px', color: C.dim }}
                    >
                      {key}{p?.unit ? ` (${p.unit})` : ''}
                      <input
                        value={paramValues[key] ?? ''}
                        placeholder={p?.default === null ? '默认' : String(p?.default ?? '')}
                        onChange={(e) => setParamValues({ ...paramValues, [key]: e.target.value })}
                        style={{ ...inputStyle, width: '110px' }}
                      />
                    </label>
                  ))}
                </div>
              )}

              {circuitMode && (
                <textarea
                  value={qasm}
                  onChange={(e) => setQasm(e.target.value)}
                  spellCheck={false}
                  style={{
                    width: '100%', height: '130px', marginTop: '8px',
                    background: '#0b1220', color: '#cbd5e1',
                    border: `1px solid ${C.border}`, borderRadius: '6px',
                    padding: '8px', fontFamily: 'ui-monospace, monospace', fontSize: '12px',
                    resize: 'vertical',
                  }}
                />
              )}
            </div>

            {/* 结果 */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
              {error && (
                <div style={{
                  padding: '8px 10px', borderRadius: '6px', marginBottom: '10px',
                  background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.4)',
                  color: '#fca5a5', fontSize: '12px', whiteSpace: 'pre-wrap',
                }}>
                  {error}
                </div>
              )}

              {!result && !error && (
                <div style={{ color: C.dim, fontSize: '12px' }}>
                  选一个实验，点「运行」。结果会带绘图、指标和单位制标记。
                </div>
              )}

              {result && (
                <>
                  <div style={{ display: 'flex', gap: '14px', alignItems: 'center', marginBottom: '10px', flexWrap: 'wrap', fontSize: '12px' }}>
                    <span style={{ color: statusStyle(result.status).color, fontWeight: 700 }}>
                      {statusStyle(result.status).label}
                    </span>
                    {result.backend && <span style={{ color: C.dim }}>backend={result.backend}</span>}
                    {result.unit_system && (
                      <span style={{ color: C.accent }}>unit_system={result.unit_system}</span>
                    )}
                    {result.approximation && (
                      <span style={{ color: result.approximation === 'exact' ? C.ok : C.warn }}>
                        approximation={result.approximation}
                      </span>
                    )}
                    {typeof result.elapsed_ms === 'number' && (
                      <span style={{ color: C.dim }}>{result.elapsed_ms} ms</span>
                    )}
                  </div>

                  {result.stdout && (
                    <pre style={preStyle}>{result.stdout}</pre>
                  )}

                  {result.metrics && Object.keys(result.metrics).length > 0 && (
                    <div style={{ marginBottom: '10px' }}>
                      <div style={{ fontSize: '11px', color: C.dim, marginBottom: '4px' }}>指标</div>
                      <table style={{ borderCollapse: 'collapse', fontSize: '12px' }}>
                        <tbody>
                          {Object.entries(result.metrics).map(([k, v]) => (
                            <tr key={k}>
                              <td style={{ padding: '2px 14px 2px 0', color: C.dim }}>{k}</td>
                              <td style={{ padding: '2px 0', fontFamily: 'ui-monospace, monospace' }}>
                                {typeof v === 'number' ? Number(v).toPrecision(6) : JSON.stringify(v)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {plotUrl && (
                    <div>
                      <div style={{ fontSize: '11px', color: C.dim, marginBottom: '4px' }}>
                        绘图（标题带「qiskit 模拟」标记）
                      </div>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={plotUrl} alt="qiskit plot" style={{ maxWidth: '100%', borderRadius: '6px', border: `1px solid ${C.border}` }} />
                    </div>
                  )}

                  {result.stderr && (
                    <details style={{ marginTop: '10px' }}>
                      <summary style={{ cursor: 'pointer', fontSize: '11px', color: C.dim }}>stderr</summary>
                      <pre style={preStyle}>{result.stderr}</pre>
                    </details>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: 'block',
  marginTop: '3px',
  background: '#0b1220',
  color: '#e2e8f0',
  border: `1px solid ${C.border}`,
  borderRadius: '6px',
  padding: '5px 8px',
  fontSize: '12px',
  width: '80px',
};

const preStyle: React.CSSProperties = {
  background: '#0b1220',
  border: `1px solid ${C.border}`,
  borderRadius: '6px',
  padding: '8px',
  fontSize: '11px',
  color: '#cbd5e1',
  whiteSpace: 'pre-wrap',
  maxHeight: '160px',
  overflow: 'auto',
  margin: '0 0 10px 0',
};

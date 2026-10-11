/**
 * R0.5 — /o/governance: kill switches, spend ESTIMATE, demo reset.
 */
import { useCallback, useEffect, useState } from "react";
import { fmtDateTime } from "../../lib/format";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface Switch {
  switch_id: string; scope_kind: "agent" | "package" | "global"; scope_ref: string | null;
  scope_label: string; tenant_id: string | null; enabled: boolean; reason: string;
  enabled_at: string; released_at: string | null; release_reason: string | null;
}
interface Opt { ref: string; label: string }
interface Spend {
  tokens_used: number; invocations: number; cost_usd: number; cap_usd: number | null;
  pct_of_cap: number | null; alerts_fired: number[];
}

const card: React.CSSProperties = { border: "1px solid var(--line, #33415a)", padding: 16, marginBottom: 20 };
const muted: React.CSSProperties = { fontSize: 12, color: "var(--slate)" };

async function post(path: string, body: unknown) {
  const r = await fetch(`${API}${path}`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

function SpendCard({ s }: { s: Spend | null }) {
  if (!s) return null;
  const pct = s.pct_of_cap ?? 0;
  return (
    <section style={card} data-testid="gov-spend-card">
      <h2 style={{ fontSize: 16 }}>Model spend <span style={muted}>· ESTIMATE from advertised rates</span></h2>
      <p data-testid="gov-spend-cost" style={{ fontSize: 28, margin: "8px 0" }}>
        US${s.cost_usd.toFixed(2)} <span style={muted}>ESTIMATE</span>
      </p>
      <p style={muted} data-testid="gov-spend-tokens">
        {s.tokens_used.toLocaleString("en-AU")} tokens · {s.invocations} calls ·{" "}
        {s.cap_usd ? `cap US$${s.cap_usd.toFixed(2)} (${pct}%)` : "no dollar cap set"}
      </p>
      {s.cap_usd && (
        <div style={{ height: 6, background: "var(--line, #33415a)", marginTop: 8 }} data-testid="gov-spend-bar">
          <div style={{ width: `${Math.min(pct, 100)}%`, height: 6, background: pct >= 80 ? "#d9534f" : "#3fa37a" }} />
        </div>
      )}
      {s.alerts_fired.length > 0 && <p style={muted} data-testid="gov-spend-alerts">Alerts fired: {s.alerts_fired.map((p) => `${p}%`).join(", ")}</p>}
    </section>
  );
}

function EngageForm({ agents, packages, onDone }: { agents: Opt[]; packages: Opt[]; onDone: (m: string) => void }) {
  const [kind, setKind] = useState<"agent" | "package" | "global">("agent");
  const [ref, setRef] = useState("intake");
  const [tenant, setTenant] = useState("");
  const [reason, setReason] = useState("");
  const opts = kind === "agent" ? agents : kind === "package" ? packages : [];
  async function submit() {
    try {
      await post("/api/o/kill-switch/engage", { scope_kind: kind, scope_ref: kind === "global" ? null : ref, tenant_id: tenant || null, reason });
      setReason(""); onDone("Kill switch engaged");
    } catch (e) { onDone(String(e)); }
  }
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginTop: 12 }} data-testid="gov-engage-form">
      <label style={muted}>Scope<br />
        <select value={kind} data-testid="gov-engage-kind"
          onChange={(e) => { const k = e.target.value as any; setKind(k); setRef(k === "package" ? packages[0]?.ref : agents[0]?.ref); }}>
          <option value="agent">One assistant</option><option value="package">A package</option><option value="global">Everything</option>
        </select>
      </label>
      {kind !== "global" && (
        <label style={muted}>Target<br />
          <select value={ref} onChange={(e) => setRef(e.target.value)} data-testid="gov-engage-ref">
            {opts.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}
          </select>
        </label>
      )}
      <label style={muted}>Tenant (blank = all)<br />
        <input value={tenant} onChange={(e) => setTenant(e.target.value)} data-testid="gov-engage-tenant" placeholder="all tenants" />
      </label>
      <label style={{ ...muted, flex: 1, minWidth: 220 }}>Reason<br />
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={1} style={{ width: "100%" }} data-testid="gov-engage-reason" />
      </label>
      <button className="k-btn k-btn--primary" disabled={reason.trim().length < 3} onClick={submit} data-testid="gov-engage-submit">Engage switch</button>
    </div>
  );
}

function SwitchRows({ rows, onRelease }: { rows: Switch[]; onRelease: (s: Switch) => void }) {
  if (!rows.length) return <p style={muted} data-testid="gov-switches-empty">No switches. Nothing is blocked.</p>;
  return (
    <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }} data-testid="gov-switches-table">
      <thead><tr style={{ textAlign: "left", ...muted }}><th>Status</th><th>Blocks</th><th>Tenant</th><th>Reason</th><th>Engaged</th><th /></tr></thead>
      <tbody>
        {rows.map((s) => (
          <tr key={s.switch_id} data-testid={`gov-switch-${s.switch_id}`} style={{ borderTop: "1px solid var(--line, #33415a)", opacity: s.enabled ? 1 : 0.55 }}>
            <td>{s.enabled ? "ENGAGED" : "released"}</td>
            <td>{s.scope_label}</td>
            <td>{s.tenant_id ? s.tenant_id.slice(0, 8) : "all"}</td>
            <td>{s.enabled ? s.reason : s.release_reason || s.reason}</td>
            <td>{fmtDateTime(s.enabled_at)}</td>
            <td>{s.enabled && <button className="k-btn" onClick={() => onRelease(s)} data-testid={`gov-release-${s.switch_id}`}>Release</button>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DemoReset({ onDone }: { onDone: (m: string) => void }) {
  const [scope, setScope] = useState<"kai" | "all_tests" | "full">("kai");
  const [busy, setBusy] = useState(false);
  async function go() {
    if (!window.confirm(`Reset for demo (${scope})? Open test requests will be closed.`)) return;
    setBusy(true);
    try { await post("/api/o/demo/reset", { scope }); onDone(`Demo reset (${scope}) done`); }
    catch (e) { onDone(String(e)); }
    setBusy(false);
  }
  return (
    <section style={card} data-testid="gov-demo-reset">
      <h2 style={{ fontSize: 16 }}>Reset for demo</h2>
      <p style={muted}>Rehearsal tool. Kai: motor policy back to seed vehicles, his open test requests closed. All tests: close stale queue items. Full: canonical seed.</p>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <select value={scope} onChange={(e) => setScope(e.target.value as any)} data-testid="gov-reset-scope">
          <option value="kai">Kai only</option><option value="all_tests">All tests</option><option value="full">Full</option>
        </select>
        <button className="k-btn" onClick={go} disabled={busy} data-testid="gov-reset-submit">{busy ? "Resetting…" : "Reset for demo"}</button>
      </div>
    </section>
  );
}

export function Governance() {
  const [rows, setRows] = useState<Switch[]>([]);
  const [agents, setAgents] = useState<Opt[]>([]);
  const [packages, setPackages] = useState<Opt[]>([]);
  const [spendData, setSpend] = useState<Spend | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(async () => {
    const [k, s] = await Promise.all([
      fetch(`${API}/api/o/kill-switch`, { credentials: "include" }).then((r) => r.json()),
      fetch(`${API}/api/o/spend`, { credentials: "include" }).then((r) => r.json()),
    ]);
    setRows(k.switches || []); setAgents(k.agents || []); setPackages(k.packages || []); setSpend(s);
  }, []);
  useEffect(() => { void load(); }, [load]);
  const done = (m: string) => { setMsg(m); void load(); };
  async function release(s: Switch) {
    const reason = window.prompt(`Release "${s.scope_label}"? Reason:`, "Drill complete");
    if (!reason) return;
    try { await post("/api/o/kill-switch/release", { switch_id: s.switch_id, reason }); done("Kill switch released"); }
    catch (e) { done(String(e)); }
  }
  return (
    <div data-testid="governance-page" style={{ maxWidth: 1100 }}>
      <p style={muted}>CONTROL · GOVERNANCE</p>
      <h1 style={{ fontSize: 28, marginBottom: 16 }}>Governance</h1>
      {msg && <p data-testid="gov-message" style={{ ...muted, marginBottom: 12 }}>{msg}</p>}
      <SpendCard s={spendData} />
      <section style={card} data-testid="gov-kill-switches">
        <h2 style={{ fontSize: 16 }}>Kill switches</h2>
        <p style={muted}>An engaged switch stops matching assistant work before any model call. Takes effect within 10 seconds.</p>
        <SwitchRows rows={rows} onRelease={release} />
        <EngageForm agents={agents} packages={packages} onDone={done} />
      </section>
      <DemoReset onDone={done} />
    </div>
  );
}

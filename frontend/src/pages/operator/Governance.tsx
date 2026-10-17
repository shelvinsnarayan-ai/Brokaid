/**
 * R0.5 — /o/governance: kill switches, spend ESTIMATE, demo reset.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fmtDateTime } from "../../lib/format";
import { BrandingEditor } from "./BrandingEditor";
import { ReleaseCheck } from "./ReleaseCheck";
import { BackgroundWork } from "./BackgroundWork";
import { AiEval } from "./AiEval";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface Switch {
  switch_id: string; scope_kind: "agent" | "package" | "global"; scope_ref: string | null;
  scope_label: string; tenant_id: string | null; enabled: boolean; reason: string;
  enabled_at: string; released_at: string | null; release_reason: string | null;
}
interface Opt { ref: string; label: string }
interface RegRow { package: string; assistant: string; class: string; autonomy: string; autonomy_label: string; data_basis: string }
interface Count { value: number; data_basis: string }

const COUNT_LABEL: Record<string, string> = {
  ledger_events: "Ledger events", open_queue_items: "Open queue items", matters: "Matters",
  things: "Things", refused_agent_calls: "Refused assistant calls", tenants: "Tenants",
};

function Basis({ b }: { b: string }) {
  return <span style={{ fontSize: 10, color: "var(--slate)", border: "1px solid var(--line, #1E2E5A)", padding: "0 6px", marginLeft: 6 }} data-testid="data-basis">{b}</span>;
}

function RegistryCard() {
  const [rows, setRows] = useState<RegRow[]>([]);
  const [counts, setCounts] = useState<Record<string, Count>>({});
  useEffect(() => {
    fetch(`${API}/api/o/registry`, { credentials: "include" }).then((r) => (r.ok ? r.json() : ({} as any)))
      .then((d) => { setRows(d.agents || []); setCounts(d.counts || {}); }).catch(() => {});
  }, []);
  return (
    <section style={card} data-testid="gov-registry">
      <h2 style={{ fontSize: 16 }}>Registry</h2>
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", margin: "8px 0 14px" }} data-testid="gov-registry-counts">
        {Object.entries(counts).map(([k, c]) => (
          <div key={k} data-testid={`gov-count-${k}`}>
            <div style={muted}>{COUNT_LABEL[k] || k}</div>
            <div style={{ fontSize: 20, fontFamily: "var(--font-mono)" }}>{c.value.toLocaleString("en-AU")}<Basis b={c.data_basis} /></div>
          </div>
        ))}
      </div>
      <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }} data-testid="gov-registry-table">
        <thead><tr style={{ textAlign: "left", ...muted }}><th>Package</th><th>Assistant</th><th>Class</th><th>Autonomy</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderTop: "1px solid var(--line, #1E2E5A)" }}>
              <td>{r.package}</td><td>{r.assistant}</td><td>{r.class}</td>
              <td title={r.autonomy_label}>{r.autonomy} · {r.autonomy_label}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={muted}>Calls from an assistant that isn't listed here are refused and logged.</p>
    </section>
  );
}
interface Spend {
  tokens_used: number; invocations: number; cost_usd: number; cap_usd: number | null;
  pct_of_cap: number | null; alerts_fired: number[];
}

const card: React.CSSProperties = { border: "1px solid var(--line, #1E2E5A)", padding: 16, marginBottom: 20 };
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
        <div style={{ height: 6, background: "var(--line, #1E2E5A)", marginTop: 8 }} data-testid="gov-spend-bar">
          <div style={{ width: `${Math.min(pct, 100)}%`, height: 6, background: pct >= 80 ? "#C0392B" : "#3fa37a" }} />
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
          <tr key={s.switch_id} data-testid={`gov-switch-${s.switch_id}`} style={{ borderTop: "1px solid var(--line, #1E2E5A)", opacity: s.enabled ? 1 : 0.55 }}>
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

interface Drill {
  drill_id: string; agent_label: string; package_label: string | null; engage_s: number; release_s: number;
  result: "pass" | "fail"; engaged_at: string; released_at: string; operator: string; recorded_at: string;
}

interface OverrideRow { user_id: string; name: string; email: string; email_override: string | null; kind: string }

function SendTests() {
  const [out, setOut] = useState<string | null>(null);
  const run = async (path: string, label: string) => {
    const r = await fetch(`${API}/api/notifications/${path}`, { method: "POST", credentials: "include" });
    const b = r.ok ? await r.json() : null;
    if (!b) return setOut(`${label}: could not send.`);
    if (path === "email-test") return setOut(`${label}: ${b.email}${b.message_id ? ` (id ${b.message_id})` : ""}`);
    const codes = (b.results || []).map((x: { status: number }) => x.status).join(", ");
    setOut(`${label}: ${b.push}${codes ? ` — push service replied ${codes}` : ""}${b.hint ? `. ${b.hint}` : ""}`);
  };
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", margin: "8px 0" }}>
      <button className="k-btn" onClick={() => void run("push-test", "Test push")} data-testid="gov-push-test">Send test push to my devices</button>
      {out && <span style={muted} data-testid="gov-test-result">{out}</span>}
    </div>
  );
}

function EmailOverrides() {
  const [rows, setRows] = useState<OverrideRow[]>([]);
  const [allow, setAllow] = useState<string[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const load = () => fetch(`${API}/api/notifications/email-overrides`, { credentials: "include" })
    .then((r) => (r.ok ? r.json() : { users: [], allowlist: [] })).then((d) => { setRows(d.users); setAllow(d.allowlist); });
  useEffect(() => { void load(); }, []);
  const save = async (u: OverrideRow) => {
    const v = (draft[u.user_id] ?? u.email_override ?? "").trim();
    await fetch(`${API}/api/notifications/email-overrides`, {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: u.user_id, email_override: v || null }),
    });
    void load();
  };
  return (
    <details style={card} data-testid="gov-email-overrides">
      <summary style={{ fontSize: 16, cursor: "pointer" }} data-testid="gov-email-overrides-toggle">Demo email and push</summary>
      <BrandingEditor />
      <SendTests />
      <p style={{ ...muted, margin: "8px 0" }} data-testid="gov-email-allowlist">
        Demo logins use .example addresses, which never receive email. Route a persona to a real inbox here.
        Only allow-listed addresses are sent to: {allow.length ? allow.join(", ") : "none set"}.
      </p>
      <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
        <tbody>
          {rows.map((u) => (
            <tr key={u.user_id} style={{ borderTop: "1px solid var(--line, #1E2E5A)" }}>
              <td style={{ padding: "6px 4px" }}>{u.name}<div style={muted}>{u.email}</div></td>
              <td style={{ padding: "6px 4px" }}>
                <input aria-label={`Email for ${u.name}`} defaultValue={u.email_override ?? ""} placeholder="real inbox (optional)"
                  onChange={(e) => setDraft({ ...draft, [u.user_id]: e.target.value })}
                  data-testid={`gov-override-input-${u.user_id}`} style={{ width: "100%" }} />
              </td>
              <td style={{ padding: "6px 4px", width: 70 }}>
                <button className="k-btn" onClick={() => void save(u)} data-testid={`gov-override-save-${u.user_id}`}>Save</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function DrillLog() {
  const [rows, setRows] = useState<Drill[]>([]);
  useEffect(() => {
    void fetch(`${API}/api/o/governance/drills`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { drills: [] })).then((d) => setRows(d.drills || []));
  }, []);
  return (
    <details style={card} data-testid="gov-drill-log">
      <summary style={{ fontSize: 16, cursor: "pointer" }} data-testid="gov-drill-log-toggle">Drill log ({rows.length})</summary>
      <p style={{ ...muted, margin: "8px 0" }}>Last 10 kill-switch drills. Recorded by the drill script. Target: effect and release under 60 seconds.</p>
      {!rows.length ? <p style={muted} data-testid="gov-drill-empty">No drills recorded yet.</p> : (
        <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }} data-testid="gov-drill-table">
          <thead><tr style={{ textAlign: "left", ...muted }}><th>Engaged</th><th>Released</th><th>Assistant</th><th>Package</th><th>Took effect</th><th>Cleared</th><th>Result</th><th>Operator</th></tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.drill_id} data-testid={`gov-drill-${d.drill_id}`} style={{ borderTop: "1px solid var(--line, #1E2E5A)" }}>
                <td>{fmtDateTime(d.engaged_at)}</td><td>{fmtDateTime(d.released_at)}</td>
                <td>{d.agent_label}</td><td>{d.package_label || "—"}</td>
                <td>{d.engage_s.toFixed(2)} s</td><td>{d.release_s.toFixed(2)} s</td>
                <td data-testid={`gov-drill-result-${d.drill_id}`}>{d.result === "pass" ? "Pass" : "Fail"}</td><td>{d.operator}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}

export function Governance() {
  const [rows, setRows] = useState<Switch[]>([]);
  const [agents, setAgents] = useState<Opt[]>([]);
  const [packages, setPackages] = useState<Opt[]>([]);
  const [spendData, setSpend] = useState<Spend | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const load = useCallback(async () => {
    const [kr, sr] = await Promise.all([
      fetch(`${API}/api/o/kill-switch`, { credentials: "include" }),
      fetch(`${API}/api/o/spend`, { credentials: "include" }),
    ]);
    if (!kr.ok || !sr.ok) { setDenied(true); return; }
    const k = await kr.json();
    setRows(k.switches || []); setAgents(k.agents || []); setPackages(k.packages || []); setSpend(await sr.json());
  }, []);
  useEffect(() => { void load(); }, [load]);
  const done = (m: string) => { setMsg(m); void load(); };
  async function release(s: Switch) {
    const reason = window.prompt(`Release "${s.scope_label}"? Reason:`, "Drill complete");
    if (!reason) return;
    try { await post("/api/o/kill-switch/release", { switch_id: s.switch_id, reason }); done("Kill switch released"); }
    catch (e) { done(String(e)); }
  }
  if (denied) return <p data-testid="governance-denied" style={muted}>Agent operations are for the Operator role.</p>;
  return (
    <div data-testid="governance-page" style={{ maxWidth: 1100 }}>
      <p style={muted}>CONTROL · GOVERNANCE</p>
      <h1 style={{ fontSize: 28, marginBottom: 16 }}>Governance</h1>
      {msg && <p data-testid="gov-message" style={{ ...muted, marginBottom: 12 }}>{msg}</p>}
      <section style={card}><ReleaseCheck /></section>
      <section style={card} data-testid="gov-invites-card"><strong>People</strong> · <Link to="/o/invites" data-testid="gov-invites-link">Invite staff and clients</Link></section>
      <section style={card}><BackgroundWork /></section>
      <section style={card}><AiEval /></section>
      <SpendCard s={spendData} />
      <RegistryCard />
      <section style={card} data-testid="gov-kill-switches">
        <h2 style={{ fontSize: 16 }}>Kill switches</h2>
        <p style={muted}>An engaged switch stops matching assistant work before any model call. Takes effect within 10 seconds.</p>
        <SwitchRows rows={rows} onRelease={release} />
        <EngageForm agents={agents} packages={packages} onDone={done} />
      </section>
      <DrillLog />
      <EmailOverrides />
      <DemoReset onDone={done} />
    </div>
  );
}

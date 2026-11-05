/** R3.10: read-only exposure report. Who used dev sign-in or demo actions since a date (demo/test tenants only). */
import { useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";
const muted = { color: "var(--slate)", fontSize: 12 } as const;

interface Report {
  since: string; tenants: string[];
  sessions: { tenant: string; at: string; via: string; persona: string | null; role: string | null; ip: string | null; served_host: string | null }[];
  events: { tenant: string; at: string; type: string; actor: string | null; persona: string | null; scope: string | null }[];
  mfa_enrolments: { tenant: string; persona: string; at: string }[];
  dev_password_logins_note: string; ip_note: string;
}

export function ExposureReport() {
  const [since, setSince] = useState("2026-11-04");
  const [rep, setRep] = useState<Report | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => {
    setErr(null);
    const r = await fetch(`${API}/api/o/exposure-report?since=${encodeURIComponent(since)}T00:00:00Z`, { credentials: "include" });
    if (r.ok) setRep(await r.json()); else setErr(r.status === 403 ? "Operator only." : "Couldn't load the report.");
  };
  const rows = (title: string, id: string, items: string[]) => (
    <div data-testid={`exposure-${id}`} style={{ marginTop: 10 }}>
      <strong>{title} ({items.length})</strong>
      {items.length ? <ul style={{ ...muted, margin: "4px 0 0 16px" }}>{items.slice(0, 200).map((t, i) => <li key={i}>{t}</li>)}</ul> : <p style={muted}>None.</p>}
    </div>
  );
  return (
    <div data-testid="gov-exposure-report">
      <h2 style={{ fontSize: 16 }}>Exposure report</h2>
      <p style={muted}>Read-only. Dev sign-in sessions, demo resets and renewals, MFA set-ups and demo logins in demo/test tenants since the date.</p>
      <label style={muted}>Since <input type="date" value={since} onChange={(e) => setSince(e.target.value)} data-testid="exposure-since" /></label>{" "}
      <button className="k-btn" onClick={() => void load()} data-testid="exposure-load">Show report</button>
      {err ? <p role="alert" style={muted} data-testid="exposure-error">{err}</p> : null}
      {rep ? <>
        <p style={muted} data-testid="exposure-tenants">Tenants checked: {rep.tenants.join(", ") || "none"}</p>
        {rows("Sign-in sessions", "sessions", rep.sessions.map((s) => `${s.at} · ${s.tenant} · ${s.persona ?? "?"} (${s.role ?? "?"}) · ${s.via}${s.ip ? ` · IP ${s.ip}` : ""}${s.served_host ? ` · ${s.served_host}` : ""}`))}
        {rows("Demo actions and logins", "events", rep.events.map((e) => `${e.at} · ${e.tenant} · ${e.type}${e.scope ? ` (${e.scope})` : ""}${e.persona ? ` · ${e.persona}` : ""}`))}
        {rows("MFA set-ups on demo users", "mfa", rep.mfa_enrolments.map((m) => `${m.at} · ${m.tenant} · ${m.persona}`))}
        <p style={muted}>{rep.dev_password_logins_note} {rep.ip_note}</p>
      </> : null}
    </div>
  );
}

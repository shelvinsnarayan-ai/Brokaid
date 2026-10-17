/** R1.9 — /platform: brokerage setup wizard, tenant status, Ops and Backups. platform_admin only (server-enforced). */
import { useState } from "react";
import { post, useLoad } from "../operator/compliance/api";
import { authCall } from "../../lib/authApi";
import { Ops } from "./Ops";
import "../../styles/import.css";
import "../../styles/platform.css";

const STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "ACT", "NT"];
const EMPTY = { slug: "", legal_name: "", abn: "", afsl: "", state: "NSW", timezone: "Australia/Sydney", sender_name: "", reply_to: "",
  operator_email: "", operator_name: "", test: false };

function Wizard({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState<any>(EMPTY);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<any>(null);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const field = (k: string, label: string, ph = "") => (
    <label className="imp-field"><span>{label}</span><input value={f[k]} placeholder={ph} onChange={(e) => set(k, e.target.value)} data-testid={`wizard-${k}`} /></label>);
  async function go() {
    try { setRes(await post("platform/tenants", f)); setErr(null); setF(EMPTY); onDone(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="imp-card" data-testid="tenant-wizard">
      <h2>New brokerage</h2>
      <div className="imp-grid">
        {field("slug", "Slug", "burmac")}{field("legal_name", "Legal name")}{field("abn", "ABN", "11 digits")}{field("afsl", "AFSL / AR number")}
        <label className="imp-field"><span>State (public holidays)</span>
          <select value={f.state} onChange={(e) => set("state", e.target.value)} data-testid="wizard-state">{STATES.map((s) => <option key={s}>{s}</option>)}</select></label>
        {field("timezone", "Timezone")}{field("sender_name", "Email sender name")}{field("reply_to", "Email reply-to")}
        {field("operator_email", "First Operator email")}{field("operator_name", "First Operator name")}
      </div>
      <label className="imp-muted"><input type="checkbox" checked={f.test} onChange={(e) => set("test", e.target.checked)} data-testid="wizard-test" /> Test tenant (never for a real brokerage)</label>
      <p className="imp-muted">Real brokerages start in setup with quoting mode manual only. Payment, compliance text and the FSG are entered by the brokerage's Operator; the checklist shows what's missing.</p>
      <button className="k-btn k-btn--primary" onClick={go} data-testid="wizard-create">Create brokerage and invite Operator</button>
      {err ? <p className="imp-err" role="alert" data-testid="wizard-error">{err}</p> : null}
      {res ? <p data-testid="wizard-result">Created {res.slug}. Operator invite: {res.invite_email_status}.</p> : null}
    </div>
  );
}

function Tenant({ t, onDone }: { t: any; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const setStatus = (status: string) => authCall(`platform/tenants/${t.tenant_id}/status`, { status, reason: `Set ${status} from /platform` }, "PUT")
    .then(onDone).catch((e) => setErr(e.message === "Something went wrong. Please try again." ? "Go-live checklist incomplete." : e.message));
  return (
    <div className="imp-card" data-testid={`tenant-${t.slug}`}>
      <h2>{t.name} <span className="imp-muted">({t.slug}){t.test ? " · test" : ""}</span></h2>
      <p data-testid={`tenant-status-${t.slug}`}>Status: <strong>{t.status}</strong> · ABN {t.abn || "—"} · {t.afsl || "—"}</p>
      <ul data-testid={`tenant-checklist-${t.slug}`}>{t.checklist.map((i: any) => <li key={i.key} data-testid={`check-${t.slug}-${i.key}`}>{i.done ? "✓" : "○"} {i.label}</li>)}</ul>
      <div style={{ display: "flex", gap: 8 }}>
        {t.status !== "live" ? <button className="k-btn" onClick={() => void setStatus("live")} data-testid={`tenant-live-${t.slug}`}>Go live</button> : null}
        {t.status !== "suspended" ? <button className="k-btn" onClick={() => void setStatus("suspended")} data-testid={`tenant-suspend-${t.slug}`}>Suspend</button>
          : <button className="k-btn" onClick={() => void setStatus("setup")} data-testid={`tenant-unsuspend-${t.slug}`}>Lift suspension</button>}
      </div>
      {err ? <p className="imp-err" role="alert" data-testid={`tenant-error-${t.slug}`}>{err}</p> : null}
    </div>
  );
}

export default function Platform() {
  const [tab, setTab] = useState("tenants");
  const { data, err, load } = useLoad<any>("platform/tenants");
  return (
    <main className="imp-page" data-testid="platform-root" style={{ padding: 32, minHeight: "100vh", background: "#0e1116", color: "#ece8de" }}>
      <header><p className="imp-muted">BROKAID PLATFORM · NO CLIENT DATA</p><h1>Platform</h1>
        <nav style={{ display: "flex", gap: 8 }}>{["tenants", "ops"].map((k) => <button key={k} className={`k-btn${tab === k ? " k-btn--primary" : ""}`} onClick={() => setTab(k)} data-testid={`platform-tab-${k}`}>{k === "tenants" ? "Brokerages" : "Ops"}</button>)}
          <button className="k-btn" onClick={() => authCall("auth/logout", {}).finally(() => { window.location.href = "/login"; })} data-testid="platform-signout">Sign out</button></nav></header>
      {err ? <p className="imp-err" role="alert" data-testid="platform-error">{err}</p> : null}
      {tab === "tenants" ? <><Wizard onDone={load} />{(data?.tenants || []).map((t: any) => <Tenant key={t.tenant_id} t={t} onDone={load} />)}</> : <Ops />}
    </main>
  );
}

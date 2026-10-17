/** R1.8 — /o/claims: the AE claims list (overdue first) with filters; lodge on behalf of a client (phone). */
import { useState } from "react";
import { Link } from "react-router-dom";
import { post, useLoad } from "./compliance/api";
import "../../styles/import.css";
import "../../styles/claims.css";

function LodgeForPhone({ onDone }: { onDone: (id: string) => void }) {
  const clients = useLoad<any>("o/clients");
  const [f, setF] = useState({ client_id: "", policy_id: "", claim_class: "motor", subtype: "accident", incident_date: "", description: "", rego: "" });
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  const list = clients.data?.clients || clients.data?.rows || [];
  async function go() {
    try {
      const r = await post<any>("o/claims", { ...f, policy_id: f.policy_id || null, incident_date: f.incident_date || null, rego: f.rego || null, channel: "phone" });
      onDone(r.claim.claim_id);
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="imp-card" data-testid="claims-lodge-phone">
      <h2>Lodge for a client (phone)</h2>
      <div className="clm-o-filters">
        <select value={f.client_id} onChange={(e) => set("client_id", e.target.value)} data-testid="claims-lodge-client">
          <option value="">Client…</option>
          {list.map((c: any) => <option key={c.client_id} value={c.client_id}>{c.display_name || c.name}</option>)}
        </select>
        <input placeholder="Policy id (optional)" value={f.policy_id} onChange={(e) => set("policy_id", e.target.value)} data-testid="claims-lodge-policy" />
        <select value={f.claim_class} onChange={(e) => set("claim_class", e.target.value)} data-testid="claims-lodge-class">
          {["motor", "property", "liability", "other"].map((k) => <option key={k}>{k}</option>)}
        </select>
        <input placeholder="Subtype (accident/theft/glass/hail/damage/third_party/other)" value={f.subtype} onChange={(e) => set("subtype", e.target.value)} data-testid="claims-lodge-subtype" />
        <input type="date" value={f.incident_date} onChange={(e) => set("incident_date", e.target.value)} data-testid="claims-lodge-date" />
        <input placeholder="Rego" value={f.rego} onChange={(e) => set("rego", e.target.value)} data-testid="claims-lodge-rego" />
      </div>
      <textarea rows={3} placeholder="What happened (client's words)" value={f.description} onChange={(e) => set("description", e.target.value)} data-testid="claims-lodge-description" />
      <button className="k-btn k-btn--primary" disabled={!f.client_id || f.description.length < 8} onClick={go} data-testid="claims-lodge-submit">Lodge claim</button>
      {err ? <p className="imp-err" role="alert" data-testid="claims-lodge-error">{err}</p> : null}
    </div>
  );
}

export default function ClaimsPage() {
  const [q, setQ] = useState({ stage: "", ae: "", insurer: "", min_age: "" });
  const [show, setShow] = useState(false);
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][]).toString();
  const { data, err } = useLoad<any>(`o/claims${qs ? `?${qs}` : ""}`);
  const set = (k: string, v: string) => setQ((x) => ({ ...x, [k]: v }));
  return (
    <section className="imp-page" data-testid="claims-page">
      <header>
        <p className="imp-muted">CLAIMS · THE CARRIED ITEM</p>
        <h1>Claims</h1>
        <p className="imp-muted">Overdue first. Coverage questions are flags for you, never decisions.{data?.quoting_mode === "manual_only" ? " This brokerage lodges claims with insurers manually." : ""}</p>
      </header>
      <div className="clm-o-filters" data-testid="claims-filters">
        <select value={q.stage} onChange={(e) => set("stage", e.target.value)} data-testid="claims-filter-stage">
          <option value="">All open stages</option>
          {(data?.stages || []).map((s: any) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <select value={q.ae} onChange={(e) => set("ae", e.target.value)} data-testid="claims-filter-ae">
          <option value="">Any AE</option><option value="me">Mine</option>
          {(data?.aes || []).map((a: any) => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}
        </select>
        <select value={q.insurer} onChange={(e) => set("insurer", e.target.value)} data-testid="claims-filter-insurer">
          <option value="">Any insurer</option>
          {(data?.insurers || []).map((i: string) => <option key={i}>{i}</option>)}
        </select>
        <select value={q.min_age} onChange={(e) => set("min_age", e.target.value)} data-testid="claims-filter-age">
          <option value="">Any age</option><option value="7">7+ days</option><option value="30">30+ days</option>
        </select>
        <button className="k-btn" onClick={() => setShow((x) => !x)} data-testid="claims-lodge-toggle">{show ? "Close" : "Lodge for a client"}</button>
      </div>
      {show ? <LodgeForPhone onDone={(id) => { window.location.href = `/o/claims/${id}`; }} /> : null}
      {err ? <p className="imp-err" role="alert" data-testid="claims-error">{err}</p> : null}
      <table className="clm-o-table" data-testid="claims-table">
        <thead><tr><th>Claim</th><th>Client</th><th>Type</th><th>Stage</th><th>Insurer</th><th>AE</th><th>Age</th><th>Flags</th></tr></thead>
        <tbody>{(data?.claims || []).map((c: any) => (
          <tr key={c.claim_id} className={c.overdue ? "overdue" : ""} data-testid={`claims-row-${c.claim_reference}`}>
            <td><Link to={`/o/claims/${c.claim_id}`} data-testid={`claims-open-${c.claim_reference}`}>{c.claim_reference}</Link>{c.overdue ? <strong data-testid={`claims-overdue-${c.claim_reference}`}> · overdue</strong> : null}{c.test ? " · test" : ""}</td>
            <td>{c.client_name}</td><td>{c.claim_class} · {c.subtype}{c.rego ? ` · ${c.rego}` : ""}</td><td>{c.stage_label}</td>
            <td>{c.insurer || "—"}</td><td>{c.ae_name || "—"}</td><td>{c.age_days}d</td><td>{c.flags.length || ""}</td>
          </tr>))}</tbody>
      </table>
      {data && !data.claims.length ? <p className="imp-muted" data-testid="claims-empty">No open claims.</p> : null}
    </section>
  );
}

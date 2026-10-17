import { useState } from "react";
import { API, day, dt, post, useLoad } from "./api";

const SOURCES = ["call", "thread", "request", "email", "in_person"];

export function Complaints({ role }: { role?: string }) {
  const { data, err, load, setErr } = useLoad<any>("o/compliance/complaints");
  const clients = useLoad<any>("o/compliance/clients").data?.clients || [];
  const [f, setF] = useState({ client_id: "", category: "service", summary: "", details: "", source: "call", source_ref: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [mv, setMv] = useState({ note: "", outcome: "", response_text: "" });
  if (!data) return <p className="imp-muted" data-testid="complaints-loading">{err || "Loading…"}</p>;
  const s = data.stats;
  async function log() {
    setErr(null);
    try { await post("o/compliance/complaints", f); setF({ ...f, summary: "", details: "" }); load(); } catch (e: any) { setErr(e.message); }
  }
  async function move(id: string, to: string) {
    setErr(null);
    try { await post(`o/compliance/complaints/${id}/move`, { to, ...mv, outcome: mv.outcome || null }); setMv({ note: "", outcome: "", response_text: "" }); load(); }
    catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="imp-page" data-testid="complaints-tab">
      <div className="imp-card" data-testid="complaints-stats">
        <h2 className="imp-h2">IDR register</h2>
        <p className="imp-muted">{s.total} complaints · {s.open} open · <b className={s.overdue ? "imp-err" : ""}>{s.overdue} overdue</b> · avg {s.avg_days_to_resolve ?? "—"} days to resolve · {s.resolved_within_30_days} within 30 days</p>
        <p className="imp-muted">By category: {Object.entries(s.by_category).map(([k, v]) => `${k} ${v}`).join(", ") || "—"} · By outcome: {Object.entries(s.by_outcome).map(([k, v]) => `${k} ${v}`).join(", ") || "—"}</p>
        <p className="imp-muted">Content visible to: {data.content_visible_to}.</p>
        {role !== "ae" ? <a className="k-btn" href={`${API}/api/o/compliance/complaints-register.csv`} data-testid="complaints-export">Export register (CSV)</a> : null}
      </div>
      <div className="imp-card" data-testid="complaint-log">
        <h2 className="imp-h2">Log a complaint</h2>
        <div className="imp-row">
          <select value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })} data-testid="complaint-client">
            <option value="">Client…</option>{clients.map((c: any) => <option key={c.client_id} value={c.client_id}>{c.display_name}</option>)}
          </select>
          <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} data-testid="complaint-category">
            {data.spec.categories.map((c: string) => <option key={c}>{c}</option>)}</select>
          <select value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} data-testid="complaint-source">
            {SOURCES.map((c) => <option key={c}>{c}</option>)}</select>
          <input placeholder="Thread / request ref (optional)" value={f.source_ref} onChange={(e) => setF({ ...f, source_ref: e.target.value })} data-testid="complaint-ref" />
        </div>
        <input placeholder="Summary" value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} data-testid="complaint-summary" />
        <textarea rows={2} placeholder="Details" value={f.details} onChange={(e) => setF({ ...f, details: e.target.value })} data-testid="complaint-details" />
        <button className="k-btn k-btn--primary" onClick={log} data-testid="complaint-log-submit">Log complaint</button>
      </div>
      {err ? <p className="imp-err" role="alert" data-testid="complaints-error">{err}</p> : null}
      <table className="imp-table" data-testid="complaints-table">
        <thead><tr><th>Received</th><th>Client</th><th>Category</th><th>Stage</th><th>Acknowledge by</th><th>Respond by</th><th /></tr></thead>
        <tbody>{data.complaints.map((m: any) => (
          <tr key={m.matter_id} data-testid={`complaint-row-${m.matter_id}`}>
            <td>{day(m.received_at)}</td><td>{m.client_name}</td><td>{m.category}</td><td data-testid={`complaint-stage-${m.matter_id}`}>{m.stage_label}</td>
            <td>{dt(m.ack_due)}</td><td className={new Date(m.response_due) < new Date() && m.next.length ? "imp-err" : ""}>{day(m.response_due)}</td>
            <td><button className="k-btn" onClick={() => setOpen(open === m.matter_id ? null : m.matter_id)} data-testid={`complaint-open-${m.matter_id}`}>Open</button></td>
          </tr>))}</tbody>
      </table>
      {data.complaints.filter((m: any) => m.matter_id === open).map((m: any) => (
        <div key={m.matter_id} className="imp-card rn-detail" data-testid="complaint-detail">
          <h3 className="imp-h3">{m.summary}</h3>
          <p>{m.details}</p>
          <p className="imp-muted">Source: {m.source}{m.source_ref ? ` (${m.source_ref})` : ""} · outcome {m.outcome || "—"}</p>
          {m.response_text ? <pre className="imp-muted" style={{ whiteSpace: "pre-wrap" }} data-testid="complaint-response">{m.response_text}</pre> : null}
          {m.next.includes("response_sent") ? (
            <>
              <textarea rows={4} placeholder="Final IDR response (the AFCA details are added automatically)" value={mv.response_text}
                onChange={(e) => setMv({ ...mv, response_text: e.target.value })} data-testid="complaint-response-text" />
              <select value={mv.outcome} onChange={(e) => setMv({ ...mv, outcome: e.target.value })} data-testid="complaint-outcome">
                <option value="">Outcome…</option>{data.spec.outcomes.map((o: string) => <option key={o}>{o}</option>)}</select>
            </>
          ) : null}
          <input placeholder="Note" value={mv.note} onChange={(e) => setMv({ ...mv, note: e.target.value })} data-testid="complaint-note" />
          <div className="imp-row">{m.next.map((to: string) => (
            <button key={to} className="k-btn" onClick={() => move(m.matter_id, to)} data-testid={`complaint-move-${to}`}>{to.replace(/_/g, " ")}</button>))}</div>
        </div>
      ))}
    </div>
  );
}

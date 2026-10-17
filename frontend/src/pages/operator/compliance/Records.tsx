import { useState } from "react";
import { day, post, useLoad } from "./api";

export function ClientsTab({ role }: { role?: string }) {
  const { data, err, load, setErr } = useLoad<any>("o/compliance/clients");
  const [reason, setReason] = useState<Record<string, string>>({});
  async function setType(id: string, client_type: string) {
    setErr(null);
    try { await post(`o/compliance/clients/${id}/type`, { client_type, reason: reason[id] || null }, "PUT"); load(); } catch (e: any) { setErr(e.message); }
  }
  async function hold(id: string, h: boolean) {
    setErr(null);
    try { await post(`o/compliance/clients/${id}/legal-hold`, { hold: h, reason: reason[id] || "Legal hold" }, "PUT"); load(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="imp-page" data-testid="clients-tab">
      <p className="imp-muted">Retail clients get the PDS + TMD with every option and need a TMD-fit check before a recommendation. Wholesale needs the test it meets.</p>
      {err ? <p className="imp-err" role="alert" data-testid="clients-error">{err}</p> : null}
      <table className="imp-table" data-testid="clients-table">
        <thead><tr><th>Client</th><th>Type</th><th>Reason / test</th><th>Legal hold</th><th /></tr></thead>
        <tbody>{(data?.clients || []).map((c: any) => (
          <tr key={c.client_id} data-testid={`client-row-${c.client_id}`}>
            <td>{c.display_name}</td><td data-testid={`client-type-${c.client_id}`}>{c.client_type}</td>
            <td><input placeholder={c.client_type_reason || "Reason / wholesale test"} value={reason[c.client_id] || ""}
              onChange={(e) => setReason({ ...reason, [c.client_id]: e.target.value })} data-testid={`client-reason-${c.client_id}`} /></td>
            <td>{c.legal_hold ? `On hold (${c.legal_hold_reason})` : "—"}</td>
            <td className="imp-row">
              <button className="k-btn" onClick={() => setType(c.client_id, c.client_type === "retail" ? "wholesale" : "retail")}
                data-testid={`client-toggle-${c.client_id}`}>Make {c.client_type === "retail" ? "wholesale" : "retail"}</button>
              {role === "principal" ? <button className="k-btn" onClick={() => hold(c.client_id, !c.legal_hold)} data-testid={`client-hold-${c.client_id}`}>
                {c.legal_hold ? "Release hold" : "Legal hold"}</button> : null}
            </td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

export function Retention({ role }: { role?: string }) {
  const { data, err, load, setErr } = useLoad<any>("o/compliance/retention");
  const ddo = useLoad<any>("o/compliance/ddo-reports").data;
  async function redact(id: string) {
    const note = window.prompt("Principal approval: why may this client's personal details be redacted?");
    if (!note) return;
    try { await post(`o/compliance/retention/${id}/redact`, { note }); load(); } catch (e: any) { setErr(e.message); }
  }
  if (!data) return <p className="imp-muted" data-testid="retention-loading">{err || "Loading…"}</p>;
  return (
    <div className="imp-page" data-testid="retention-tab">
      <div className="imp-card">
        <h2 className="imp-h2">Records past retention</h2>
        <p className="imp-muted">Policy: {Object.entries(data.years).map(([k, v]) => `${k} ${v} years`).join(", ")} after the relationship or policy ends.
          Nothing is deleted automatically. The ledger is append-only; with Principal approval, only the personal details on the client record are redacted, and that is recorded on the ledger.</p>
        <p className="imp-muted" data-testid="retention-summary">{data.past_retention.length} past retention · {data.on_hold} on legal hold · {data.checked} checked</p>
      </div>
      {err ? <p className="imp-err" role="alert" data-testid="retention-error">{err}</p> : null}
      <table className="imp-table" data-testid="retention-table">
        <thead><tr><th>Type</th><th>Record</th><th>Ended</th><th>Retain until</th><th>Hold</th><th /></tr></thead>
        <tbody>{data.past_retention.map((r: any) => (
          <tr key={`${r.record_type}-${r.id}`} data-testid={`retention-row-${r.id}`}>
            <td>{r.record_type}</td><td>{r.label}</td><td>{day(r.ended)}</td><td>{day(r.retain_until)}</td><td>{r.legal_hold ? "Yes" : "—"}</td>
            <td>{r.redactable && role === "principal" ? <button className="k-btn" onClick={() => redact(r.id)} data-testid={`retention-redact-${r.id}`}>Redact PII</button> : null}</td>
          </tr>))}</tbody>
      </table>
      <div className="imp-card" data-testid="ddo-reports">
        <h2 className="imp-h2">DDO: significant dealings outside the TMD</h2>
        {(ddo?.reports || []).length ? (ddo.reports.map((r: any) => (
          <p key={r.report_id} className="imp-muted">{day(r.at)} · {r.client_name} · {r.insurer} {r.product} · {r.tmd_reason}</p>)))
          : <p className="imp-muted">None logged.</p>}
      </div>
    </div>
  );
}

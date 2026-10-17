import { useState } from "react";
import { day, post, useLoad } from "./api";

export function Privacy({ role }: { role?: string }) {
  const { data, err, load, setErr } = useLoad<any>(role === "principal" ? "o/compliance/privacy" : "o/compliance/settings");
  const [note, setNote] = useState("");
  if (role !== "principal") return <p className="imp-muted" data-testid="privacy-denied">Privacy requests are handled by a Principal.</p>;
  if (!data) return <p className="imp-muted" data-testid="privacy-loading">{err || "Loading…"}</p>;
  async function move(id: string, to: string, outcome: string | null) {
    setErr(null);
    try { await post(`o/compliance/privacy/${id}/move`, { to, note, outcome }); load(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="imp-page" data-testid="privacy-tab">
      <p className="imp-muted">{data.stats.total} requests · {data.stats.open} open · {data.stats.overdue} overdue (30-day response clock)</p>
      <input placeholder="Note for the next action" value={note} onChange={(e) => setNote(e.target.value)} data-testid="privacy-note" />
      {err ? <p className="imp-err" role="alert" data-testid="privacy-error">{err}</p> : null}
      <table className="imp-table" data-testid="privacy-table">
        <thead><tr><th>Received</th><th>Client</th><th>Request</th><th>Details</th><th>Due</th><th>Stage</th><th>Export</th><th /></tr></thead>
        <tbody>{data.requests.map((m: any) => (
          <tr key={m.matter_id} data-testid={`privacy-row-${m.matter_id}`}>
            <td>{day(m.received_at)}</td><td>{m.client_name}</td><td>{m.category}</td><td>{m.details || "—"}</td><td>{day(m.response_due)}</td>
            <td data-testid={`privacy-stage-${m.matter_id}`}>{m.stage_label}</td>
            <td>{m.export ? `Ready ${day(m.export.created_at)} (expires ${day(m.export.expires_at)})` : m.category === "access" ? "Building…" : "—"}</td>
            <td className="imp-row">{m.next.map((to: string) => (
              <button key={to} className="k-btn" data-testid={`privacy-move-${to}-${m.matter_id}`}
                onClick={() => move(m.matter_id, to, to === "completed" ? (m.category === "access" ? "granted" : m.category === "correction" ? "corrected" : "resolved") : to === "refused" ? "refused" : null)}>
                {to.replace("_", " ")}</button>))}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

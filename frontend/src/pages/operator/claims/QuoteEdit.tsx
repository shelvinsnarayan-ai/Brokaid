/** R3.8 — correct the client-facing summary of a repair quote (AE / Principal; ledgered). */
import { useState } from "react";
import { quoteLine, type Quote } from "../QuoteFields";

const API = (import.meta as any).env.VITE_API_BASE || "";

export function QuoteEdit({ claimId, d, onDone }: { claimId: string; d: { file_id: string; quote?: Quote | null }; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState<Quote>(d.quote || {});
  const [err, setErr] = useState<string | null>(null);
  async function save() {
    setErr(null);
    const r = await fetch(`${API}/api/o/claims/${claimId}/files/${d.file_id}/quote`, { method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repairer: v.repairer || "", total: v.total || "", quote_no: v.quote_no || null, quote_date: v.quote_date || null }) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); setErr(r.status === 403 ? "Only an AE or Principal can change a quote summary." : j.detail?.detail || "Couldn't save."); return; }
    setOpen(false); onDone();
  }
  const f = (k: keyof Quote, label: string, type = "text") => (
    <label className="imp-muted" style={{ display: "inline-flex", flexDirection: "column", marginRight: 8 }}>{label}
      <input type={type} value={(v[k] as string) || ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} data-testid={`claim-quote-edit-${k}-${d.file_id}`} /></label>
  );
  return (
    <span style={{ display: "block", marginLeft: 12 }} data-testid={`claim-quote-${d.file_id}`}>
      <span className="imp-muted" data-testid={`claim-quote-line-${d.file_id}`}>{d.quote ? `Client summary: ${quoteLine(d.quote)}` : "No client summary (add repairer and total to show one)"}</span>{" "}
      <button className="k-btn" onClick={() => setOpen(!open)} data-testid={`claim-quote-edit-btn-${d.file_id}`}>{d.quote ? "Edit summary" : "Add summary"}</button>
      {open ? <span style={{ display: "block", marginTop: 6 }}>
        {f("repairer", "Repairer")}{f("total", "Total A$ incl GST")}{f("quote_no", "Quote number")}{f("quote_date", "Quote date", "date")}
        <button className="k-btn k-btn--primary" onClick={() => void save()} data-testid={`claim-quote-save-${d.file_id}`}>Save</button>
        {err ? <span role="alert" className="imp-muted" data-testid={`claim-quote-error-${d.file_id}`}> {err}</span> : null}
      </span> : null}
    </span>
  );
}

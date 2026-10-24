/** R3.8 — "Repair quote received" card on the claim tracker. Repairer's figures only; nothing about cover, excess or approval. */
import { useState } from "react";
import { fmtDate } from "../../lib/format";

const API = (import.meta as any).env.VITE_API_BASE || "";

export interface QuoteItem { file_id: string; repairer: string; total: string; quote_no?: string | null; quote_date?: string | null; received_at: string; filename: string }
export interface QuoteSummary { latest: QuoteItem; count: number; items: QuoteItem[] }

export const aud = (t: string) => `A$${Number(t).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} incl GST`;
const when = (q: QuoteItem) => (q.quote_date ? `Quote dated ${fmtDate(q.quote_date)}` : `Received ${fmtDate(q.received_at)}`);

function Row({ q, claimId, i }: { q: QuoteItem; claimId: string; i: number }) {
  return (
    <li className="mer-row__s" data-testid={`mer-quote-item-${i}`} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "6px 0" }}>
      <span>{q.repairer} · {aud(q.total)} · {when(q)}</span>
      <a href={`${API}/api/c/claims/${claimId}/files/${q.file_id}`} target="_blank" rel="noreferrer" data-testid={`mer-quote-item-view-${i}`}>View</a>
    </li>
  );
}

export function QuoteCard({ s, claimId }: { s?: QuoteSummary | null; claimId: string }) {
  const [all, setAll] = useState(false);
  if (!s) return null;
  const q = s.latest;
  return (
    <section className="mer-sec" data-testid="mer-quote-card">
      <div className="mer-card" style={{ padding: 14 }}>
        <p style={{ margin: 0, fontWeight: 700 }} data-testid="mer-quote-title">Repair quote received</p>
        <p style={{ margin: "6px 0 0" }} data-testid="mer-quote-repairer">{q.repairer}{q.quote_no ? <span className="mer-row__s"> · {q.quote_no}</span> : null}</p>
        <p className="mer-h" style={{ margin: "4px 0 0", fontSize: 22 }} data-testid="mer-quote-total">{aud(q.total)}</p>
        <p className="mer-row__s" style={{ margin: "2px 0 0" }} data-testid="mer-quote-date">{when(q)}</p>
        <a className="mer-btn" style={{ marginTop: 10, display: "inline-flex" }} href={`${API}/api/c/claims/${claimId}/files/${q.file_id}`} target="_blank" rel="noreferrer"
          data-testid="mer-quote-view">View quote</a>
        <p className="mer-row__s" style={{ margin: "10px 0 0" }} data-testid="mer-quote-disclaimer">This is the repairer's quote. Your insurer decides what's covered and any excess you pay.</p>
        {s.count > 1 ? <>
          <button type="button" className="mer-row__s" onClick={() => setAll(!all)} aria-expanded={all} data-testid="mer-quote-see-all"
            style={{ background: "none", border: 0, padding: 0, marginTop: 8, textDecoration: "underline", cursor: "pointer" }}>{s.count} quotes received · {all ? "hide" : "see all"}</button>
          {all ? <ul style={{ listStyle: "none", padding: 0, margin: "6px 0 0" }} data-testid="mer-quote-list">{s.items.map((x, i) => <Row key={x.file_id} q={x} claimId={claimId} i={i} />)}</ul> : null}
        </> : null}
      </div>
    </section>
  );
}

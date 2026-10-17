import { useEffect, useState } from "react";
import { api, get, money, Payable, STATUS_LABEL, Txn } from "./api";

function Actions({ t, open, done }: { t: Txn; open: Payable[]; done: () => void }) {
  const [pick, setPick] = useState("");
  const [insurer, setInsurer] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const run = (p: Promise<unknown>) => p.then(done).catch((e: any) => setErr(e.message));
  const id = t.txn_id.slice(0, 8);
  if (t.match) {
    return (
      <div className="imp-row">
        {t.match.review_required && !t.match.reviewed ? (
          <button className="k-btn" onClick={() => run(api(`o/bank/txns/${t.txn_id}/review`))} data-testid={`bank-review-${id}`}>Looks right</button>
        ) : null}
        <button className="k-btn" data-testid={`bank-unmatch-${id}`} onClick={() => {
          const reason = window.prompt("Why are you unmatching this line?");
          if (reason) run(api(`o/bank/txns/${t.txn_id}/unmatch`, { reason }));
        }}>Unmatch</button>
        {err ? <span className="imp-err">{err}</span> : null}
      </div>
    );
  }
  if (t.status === "outgoing") {
    return (
      <div className="imp-row">
        <input placeholder="Insurer paid" value={insurer} onChange={(e) => setInsurer(e.target.value)} data-testid={`bank-out-insurer-${id}`} />
        <button className="k-btn" disabled={!insurer} onClick={() => run(api(`o/bank/txns/${t.txn_id}/payment`, { kind: "insurer_payment", insurer }))} data-testid={`bank-out-insurer-save-${id}`}>Paid to insurer</button>
        <button className="k-btn" onClick={() => run(api(`o/bank/txns/${t.txn_id}/payment`, { kind: "refund" }))} data-testid={`bank-out-refund-${id}`}>Refund</button>
        {err ? <span className="imp-err">{err}</span> : null}
      </div>
    );
  }
  if (t.direction !== "in") return null;
  return (
    <div className="imp-row">
      {t.suggestions.map((s) => (
        <button key={s.payable_id} className="k-btn" onClick={() => run(api(`o/bank/txns/${t.txn_id}/match`, { payable_id: s.payable_id }))} data-testid={`bank-accept-${id}-${s.crn}`}>
          Match {s.client} · {money(s.amount)} ({s.why})
        </button>
      ))}
      <select value={pick} onChange={(e) => setPick(e.target.value)} data-testid={`bank-manual-${id}`}>
        <option value="">Match to…</option>
        {open.map((p) => <option key={p.payable_id} value={p.payable_id}>{p.client} · CRN {p.crn} · {money(Number(p.amount) - Number(p.received))}</option>)}
      </select>
      <button className="k-btn" disabled={!pick} onClick={() => run(api(`o/bank/txns/${t.txn_id}/match`, { payable_id: pick }))} data-testid={`bank-manual-save-${id}`}>Match</button>
      {err ? <span className="imp-err">{err}</span> : null}
    </div>
  );
}

export function Lines({ tick, changed }: { tick: number; changed: () => void }) {
  const [filter, setFilter] = useState("");
  const [rows, setRows] = useState<Txn[]>([]);
  const [open, setOpen] = useState<Payable[]>([]);
  const load = () => {
    get<{ txns: Txn[] }>(`o/bank/txns${filter ? `?status=${filter}` : ""}`).then((d) => setRows(d.txns)).catch(() => {});
    get<{ payables: Payable[] }>("o/bank/payables?state=owed,advised,partial").then((d) => setOpen(d.payables)).catch(() => {});
  };
  useEffect(load, [filter, tick]);
  const tabs = [["", "All"], ["unmatched,suggested", "To match"], ["matched,partial,overpaid", "Matched"], ["outgoing,allocated", "Money out"]];
  return (
    <section className="imp-card" data-testid="bank-lines">
      <h2 className="imp-h2">Statement lines</h2>
      <p className="imp-muted">A line with a valid payment reference (CRN) matches automatically and is listed for review. Anything else needs a person to confirm.</p>
      <div className="imp-tabs">{tabs.map(([q, l]) => <button key={l} className={`k-btn ${filter === q ? "k-btn--primary" : ""}`} onClick={() => setFilter(q)} data-testid={`bank-lines-tab-${l.toLowerCase().replace(/\s+/g, "-")}`}>{l}</button>)}</div>
      <table className="imp-table">
        <thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>{rows.map((t) => (
          <tr key={t.txn_id} data-testid={`bank-line-${t.txn_id.slice(0, 8)}`}>
            <td>{t.date}</td><td>{t.description}</td><td>{money(t.amount)}</td>
            <td data-testid={`bank-line-status-${t.txn_id.slice(0, 8)}`}>{STATUS_LABEL[t.status] ?? t.status}{t.match ? ` · CRN ${t.match.crn}${t.match.kind === "auto_crn" ? (t.match.reviewed ? " · reviewed" : " · auto, review") : " · by a person"}` : ""}</td>
            <td><Actions t={t} open={open} done={() => { load(); changed(); }} /></td>
          </tr>
        ))}</tbody>
      </table>
    </section>
  );
}

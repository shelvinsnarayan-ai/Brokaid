import { useEffect, useState } from "react";
import { api, fileUrl, get, money, Payable } from "./api";

const STATE: Record<string, string> = { owed: "Owed", advised: "Advised (client says paid)", partial: "Part received", received: "Received (bank confirmed)", overpaid: "Overpaid" };

export function Owed({ tick }: { tick: number }) {
  const [rows, setRows] = useState<Payable[]>([]);
  useEffect(() => { get<{ payables: Payable[] }>("o/bank/payables").then((d) => setRows(d.payables)).catch(() => {}); }, [tick]);
  return (
    <section className="imp-card" data-testid="bank-owed">
      <h2 className="imp-h2">Money owed by clients</h2>
      <table className="imp-table">
        <thead><tr><th>Client</th><th>CRN</th><th>Amount</th><th>Received</th><th>State</th><th>Due</th></tr></thead>
        <tbody>{rows.map((p) => (
          <tr key={p.payable_id} data-testid={`bank-owed-${p.crn}`}>
            <td>{p.client ?? "—"}</td><td><code>{p.crn}</code></td><td>{money(p.amount)}</td><td>{money(p.received)}</td>
            <td className={`bank-state bank-state--${p.state}`} data-testid={`bank-owed-state-${p.crn}`}>{STATE[p.state] ?? p.state}</td><td>{p.due_date}</td>
          </tr>
        ))}</tbody>
      </table>
    </section>
  );
}

export function Trust({ tick, changed }: { tick: number; changed: () => void }) {
  const [d, setD] = useState<any>(null);
  const [f, setF] = useState({ kind: "insurer_payment", amount: "", insurer: "", note: "" });
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get("o/bank/trust").then(setD).catch(() => {}); }, [tick]);
  async function save() {
    setErr(null);
    try { await api("o/bank/trust/payments", f); setF({ ...f, amount: "", note: "" }); changed(); } catch (e: any) { setErr(e.message); }
  }
  if (!d) return null;
  return (
    <section className="imp-card" data-testid="bank-trust">
      <h2 className="imp-h2">Trust account record · balance <span data-testid="bank-trust-total">{money(d.total)}</span></h2>
      <p className="imp-muted" data-testid="bank-trust-label">{d.label}</p>
      <div className="imp-row">
        <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} data-testid="trust-pay-kind"><option value="insurer_payment">Paid to insurer</option><option value="refund">Refund to client</option></select>
        <input placeholder="Amount" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} data-testid="trust-pay-amount" />
        <input placeholder="Insurer" value={f.insurer} onChange={(e) => setF({ ...f, insurer: e.target.value })} data-testid="trust-pay-insurer" />
        <input placeholder="Note" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} data-testid="trust-pay-note" />
        <button className="k-btn" onClick={save} data-testid="trust-pay-save">Record payment out</button>
        {err ? <span className="imp-err" data-testid="trust-pay-error">{err}</span> : null}
      </div>
      <h3 className="imp-h3">Balance per client</h3>
      <table className="imp-table"><tbody>{d.per_client.map((c: any) => <tr key={c.client_id} data-testid={`trust-client-${c.client_id.slice(0, 8)}`}><td>{c.client}</td><td>{money(c.balance)}</td></tr>)}</tbody></table>
      <h3 className="imp-h3">Entries</h3>
      <table className="imp-table">
        <thead><tr><th>Date</th><th>Kind</th><th>Amount</th><th>On bank statement</th><th>Note</th></tr></thead>
        <tbody>{d.entries.map((e: any) => <tr key={e.entry_id}><td>{e.date}</td><td>{e.kind.replace("_", " ")}</td><td>{money(e.amount)}</td><td>{e.presented ? "Yes" : "Not yet"}</td><td>{e.note}{e.insurer ? ` · ${e.insurer}` : ""}</td></tr>)}</tbody>
      </table>
    </section>
  );
}

export function Recon({ tick, role, changed }: { tick: number; role?: string; changed: () => void }) {
  const [asAt, setAsAt] = useState(new Date().toISOString().slice(0, 10));
  const [r, setR] = useState<any>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get(`o/bank/reconciliation?as_at=${asAt}`).then(setR).catch((e) => setErr(e.message)); }, [asAt, tick]);
  const monthEnd = () => { const d = new Date(); d.setDate(0); setAsAt(d.toISOString().slice(0, 10)); };
  async function sign() {
    setErr(null);
    try { await api("o/bank/reconciliation/sign-off", { as_at: asAt, note }); setNote(""); changed(); } catch (e: any) { setErr(e.message); }
  }
  if (!r) return null;
  return (
    <section className="imp-card" data-testid="bank-recon">
      <h2 className="imp-h2">Reconciliation</h2>
      <p className="imp-muted">{r.label}</p>
      <div className="imp-row">
        <label>As at <input type="date" value={asAt} onChange={(e) => setAsAt(e.target.value)} data-testid="recon-as-at" /></label>
        <button className="k-btn" onClick={() => setAsAt(new Date().toISOString().slice(0, 10))} data-testid="recon-today">Today</button>
        <button className="k-btn" onClick={monthEnd} data-testid="recon-month-end">Last month end</button>
        <a className="k-btn" href={fileUrl(`o/bank/reconciliation/export.csv?as_at=${asAt}`)} data-testid="recon-export-csv">Export CSV</a>
        <a className="k-btn" href={fileUrl(`o/bank/reconciliation/export.pdf?as_at=${asAt}`)} data-testid="recon-export-pdf">Export PDF</a>
      </div>
      <table className="imp-table" data-testid="recon-table">
        <tbody>
          <tr><td>Bank balance (statement)</td><td data-testid="recon-bank">{money(r.bank_balance)}</td></tr>
          <tr><td>Trust record balance</td><td data-testid="recon-trust">{money(r.trust_balance)}</td></tr>
          {r.items.map((i: any) => <tr key={i.key} data-testid={`recon-item-${i.key}`}><td>+ {i.label}</td><td>{money(i.amount)}</td></tr>)}
          <tr><td><strong>Unexplained difference</strong></td><td className={r.balanced ? "" : "imp-err"} data-testid="recon-unexplained">{money(r.unexplained)} {r.balanced ? "· balances" : "· doesn't balance"}</td></tr>
        </tbody>
      </table>
      <p className="imp-muted" data-testid="recon-open-items">Open items: {r.open_items.unmatched} unmatched receipt(s), {r.open_items.outgoing} unrecorded payment(s) out, {r.open_items.unpresented} trust payment(s) not yet on a statement.</p>
      {role === "principal" ? (
        <div className="imp-row">
          <input placeholder={r.balanced ? "Note (optional)" : "Explain the difference (required)"} value={note} onChange={(e) => setNote(e.target.value)} data-testid="recon-note" />
          <button className="k-btn k-btn--primary" onClick={sign} data-testid="recon-sign-off">Sign off as at {asAt}</button>
        </div>
      ) : <p className="imp-muted" data-testid="recon-signoff-principal-only">A Principal signs off the reconciliation.</p>}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="recon-error">{err}</p> : null}
      {r.signoffs.length ? <ul data-testid="recon-signoffs">{r.signoffs.map((s: any) => <li key={s.signoff_id}>Signed off as at {s.as_at} · unexplained {money(s.summary.unexplained)}{s.note ? ` · ${s.note}` : ""}</li>)}</ul> : null}
    </section>
  );
}

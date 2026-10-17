/** R1.6 — manual_only tenants: the AE keys in the real insurer quote for a parked change. */
import { useState } from "react";

const API = import.meta.env.VITE_API_BASE || "";

export function EnterInsurerQuote({ envelopeId, onDone }: { envelopeId: string; onDone: () => void }) {
  const [f, setF] = useState({ premium_change_amount: "", insurer: "", reference: "" });
  const [msg, setMsg] = useState<string | null>(null);
  async function save() {
    setMsg(null);
    const r = await fetch(`${API}/api/o/queue/${envelopeId}/insurer-quote`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { setMsg(typeof b.detail === "string" ? b.detail : `Error ${r.status}`); return; }
    setMsg(`Quote recorded (${b.quote.data_basis}). ${b.next}`);
    onDone();
  }
  return (
    <div data-testid="dcard-enter-quote" style={{ display: "grid", gap: 6, marginTop: 8 }}>
      <strong>Enter insurer quote</strong>
      <span style={{ fontSize: 12, color: "var(--slate)" }}>This brokerage uses real insurer quotes only. Enter what the insurer gave you.</span>
      <input placeholder="Premium change A$ (e.g. 85.00)" value={f.premium_change_amount}
        onChange={(e) => setF({ ...f, premium_change_amount: e.target.value })} data-testid="dcard-quote-amount" />
      <input placeholder="Insurer" value={f.insurer} onChange={(e) => setF({ ...f, insurer: e.target.value })} data-testid="dcard-quote-insurer" />
      <input placeholder="Insurer reference" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} data-testid="dcard-quote-ref" />
      <button className="k-btn k-btn--primary" onClick={save} data-testid="dcard-quote-save">Record quote</button>
      {msg ? <span style={{ fontSize: 12 }} data-testid="dcard-quote-msg">{msg}</span> : null}
    </div>
  );
}

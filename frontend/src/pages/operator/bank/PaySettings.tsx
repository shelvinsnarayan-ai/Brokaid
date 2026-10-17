import { useEffect, useState } from "react";
import { api, get } from "./api";

export function PaySettings({ role }: { role?: string }) {
  const [f, setF] = useState({ account_name: "", bsb: "", account_number: "", biller_code: "", reference_rule: "crn" });
  const [rules, setRules] = useState<Record<string, string>>({});
  const [set, setSet] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    get("o/payment-settings").then((d) => { setRules(d.rules); if (d.settings) { setF({ ...f, ...d.settings, biller_code: d.settings.biller_code || "" }); setSet(true); } }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function save() {
    setErr(null); setMsg(null);
    try { await api("o/payment-settings", f, "PUT"); setSet(true); setMsg("Saved. Clients now see these details on the Pay screen."); } catch (e: any) { setErr(e.message); }
  }
  const ro = role !== "operator";
  const field = (k: keyof typeof f, label: string, ph = "") => (
    <label className="imp-row">{label}<input value={f[k]} placeholder={ph} disabled={ro} onChange={(e) => setF({ ...f, [k]: e.target.value })} data-testid={`pay-settings-${k}`} /></label>
  );
  return (
    <section className="imp-card" data-testid="pay-settings">
      <h2 className="imp-h2">Payment details clients see</h2>
      <p className="imp-muted" data-testid="pay-settings-status">{set ? "Set — the client Pay screen shows these details." : "Not set — the Pay screen shows MOCKED details until you save real ones."}</p>
      {field("account_name", "Trust account name")}
      {field("bsb", "BSB", "062-000")}
      {field("account_number", "Account number")}
      {field("biller_code", "BPAY biller code (optional)")}
      <label className="imp-row">Reference rule
        <select value={f.reference_rule} disabled={ro} onChange={(e) => setF({ ...f, reference_rule: e.target.value })} data-testid="pay-settings-rule">
          {Object.entries(rules).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <p className="imp-muted">Every amount owed gets its own reference (CRN) with a BPAY MOD10V01 (Luhn) check digit.</p>
      {ro ? <p className="imp-muted" data-testid="pay-settings-readonly">Only the Operator can change these.</p> : <button className="k-btn k-btn--primary" onClick={save} data-testid="pay-settings-save">Save payment details</button>}
      {msg ? <p className="brokaid-form__msg" data-testid="pay-settings-msg">{msg}</p> : null}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="pay-settings-error">{err}</p> : null}
    </section>
  );
}

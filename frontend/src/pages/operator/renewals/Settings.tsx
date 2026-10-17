import { useState } from "react";
import { api } from "./api";

const STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "ACT", "NT"];

export function RenewalSettings({ role, settings, onSaved }: { role?: string; settings: { renewal_lead_days: number; holiday_state: string; quoting_mode: string; mocked_allowed_possible: boolean }; onSaved: () => void }) {
  const [f, setF] = useState({ renewal_lead_days: settings.renewal_lead_days, holiday_state: settings.holiday_state, quoting_mode: settings.quoting_mode });
  const [msg, setMsg] = useState<string | null>(null);
  const ro = role !== "operator";
  async function save() {
    setMsg(null);
    try { await api("o/renewals-settings", f, "PUT"); setMsg("Saved."); onSaved(); } catch (e: any) { setMsg(e.message); }
  }
  return (
    <section className="imp-card" data-testid="renewal-settings">
      <h2 className="imp-h2">Renewal settings</h2>
      <label className="imp-row">Start renewals this many days before expiry (45–90)
        <input type="number" min={45} max={90} value={f.renewal_lead_days} disabled={ro}
          onChange={(e) => setF({ ...f, renewal_lead_days: Number(e.target.value) })} data-testid="renewal-settings-lead" />
      </label>
      <label className="imp-row">Public holidays for business days (national + this state)
        <select value={f.holiday_state} disabled={ro} onChange={(e) => setF({ ...f, holiday_state: e.target.value })} data-testid="renewal-settings-state">
          {STATES.map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      <label className="imp-row">Insurer quotes
        <select value={f.quoting_mode} disabled={ro || !settings.mocked_allowed_possible} onChange={(e) => setF({ ...f, quoting_mode: e.target.value })}
          data-testid="renewal-settings-quoting">
          <option value="manual_only">Real quotes only (entered by an AE)</option>
          {settings.mocked_allowed_possible ? <option value="mocked_allowed">Mocked quotes allowed (demo / test)</option> : null}
        </select>
      </label>
      {ro ? <p className="imp-muted" data-testid="renewal-settings-readonly">Only the Operator can change these.</p>
        : <button className="k-btn k-btn--primary" onClick={save} data-testid="renewal-settings-save">Save settings</button>}
      {msg ? <p className="imp-muted" data-testid="renewal-settings-msg">{msg}</p> : null}
    </section>
  );
}

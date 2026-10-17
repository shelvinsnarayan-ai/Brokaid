/** R1.6 — /o/renewals: the working renewal pipeline. Overdue first; filters by stage, AE and due window. */
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useOperatorStore } from "../../state/operatorStore";
import { Basis } from "./stubKit";
import { RenewalDetail } from "./renewals/Detail";
import { RenewalSettings } from "./renewals/Settings";
import { api, get } from "./renewals/api";
import "../../styles/import.css";
import "../../styles/renewals.css";

interface Row {
  policy_id: string; client: string; policy: string; insurer: string; expiry: string; days_to_go: { value: number; data_basis: string };
  expired: boolean; next_step: string; renewal_id: string | null; stage: string; stage_label: string; overdue: boolean;
  lapse_risk: boolean; needs_review: boolean; ae: string | null; advice_pending: boolean;
}

export function Renewals() {
  const role = useOperatorStore((s) => s.session)?.session?.active_role;
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<any>(null);
  const [f, setF] = useState({ stage: "", ae: "", due: "" });
  const [err, setErr] = useState<string | null>(null);
  const open = params.get("r");
  const load = useCallback(() => {
    const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    get(`o/renewals${q ? `?${q}` : ""}`).then(setData).catch((e) => setErr(e.message));
  }, [f]);
  useEffect(load, [load]);
  async function startEarly(policy_id: string) {
    setErr(null);
    try { const d = await api("o/renewals/start", { policy_id }); setParams({ r: d.renewal.renewal_id }); load(); } catch (e: any) { setErr(e.message); }
  }
  if (!data) return <p data-testid="renewals-loading" className="imp-muted">{err || "Loading…"}</p>;
  const rows: Row[] = data.renewals;
  const sel = (k: keyof typeof f, opts: [string, string][], label: string) => (
    <label className="imp-row">{label}
      <select value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} data-testid={`renewals-filter-${k}`}>
        <option value="">All</option>{opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
  return (
    <section className="ledger-page imp-page" data-testid="renewals-page">
      <header>
        <p className="imp-muted">WORK · RENEWALS</p>
        <h1 className="ledger-page__title">Renewals</h1>
        <p className="imp-muted" data-testid="renewals-count">
          {rows.length} policies · starts {data.settings.renewal_lead_days} days before expiry · business days use {data.settings.holiday_state} public holidays
          <Basis b="renewal pipeline" />
        </p>
      </header>
      <div className="rn-stages" data-testid="renewals-stage-counts">
        {Object.entries(data.stage_counts as Record<string, number>).filter(([, n]) => n).map(([k, n]) => (
          <button key={k} className={`rn-chip ${f.stage === k ? "rn-chip--on" : ""}`} onClick={() => setF({ ...f, stage: f.stage === k ? "" : k })}
            data-testid={`renewals-stage-chip-${k}`}>{(data.stages[k] || "Not started")} <b>{n}</b></button>
        ))}
      </div>
      <div className="imp-tabs">
        {sel("stage", [["not_started", "Not started"], ...Object.entries(data.stages as Record<string, string>)], "Stage")}
        {sel("ae", data.aes.map(([id, n]: [string, string]) => [id, n || "AE"]), "AE")}
        {sel("due", [["overdue", "Overdue / stuck"], ["30", "Next 30 days"], ["60", "Next 60 days"], ["90", "Next 90 days"]], "Due")}
      </div>
      {err ? <p className="imp-err" role="alert" data-testid="renewals-error">{err}</p> : null}
      {open ? <RenewalDetail id={open} role={role} mode={data.settings.quoting_mode} onClose={() => { setParams({}); load(); }} onChanged={load} /> : null}
      {!rows.length ? <p className="imp-muted" data-testid="renewals-empty">No policies match.</p> : (
        <table className="imp-table" data-testid="renewals-table">
          <thead><tr>{["", "Client", "Policy", "Insurer", "Expiry", "Days", "Stage", "AE", "Next step", ""].map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.policy_id} data-testid={`renewal-row-${r.policy_id}`} className={r.overdue ? "rn-row--overdue" : ""}>
                <td>{r.overdue ? <span className="rn-flag" data-testid={`renewal-overdue-${r.policy_id}`}>{r.lapse_risk ? "Lapse risk" : "Overdue"}</span> : null}</td>
                <td>{r.client}</td><td>{r.policy}</td><td>{r.insurer}</td>
                <td className="rn-mono" data-testid={`renewal-expiry-${r.policy_id}`}>{r.expiry}</td>
                <td className="rn-mono" data-testid={`renewal-days-${r.policy_id}`}>{r.expired ? "Expired" : r.days_to_go.value}</td>
                <td data-testid={`renewal-stage-${r.policy_id}`}><span className={`rn-stage rn-stage--${r.stage}`}>{r.stage_label}</span>
                  {r.needs_review ? <span className="imp-warn"> · review</span> : null}{r.advice_pending ? <span className="imp-warn"> · advice approval</span> : null}</td>
                <td>{r.ae || "—"}</td>
                <td data-testid={`renewal-next-${r.policy_id}`}>{r.next_step}</td>
                <td>{r.renewal_id
                  ? <button className="k-btn" onClick={() => setParams({ r: r.renewal_id! })} data-testid={`renewal-open-${r.policy_id}`}>Open</button>
                  : !r.expired && role !== "operator" ? <button className="k-btn" onClick={() => startEarly(r.policy_id)} data-testid={`renewal-start-${r.policy_id}`}>Start early</button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <RenewalSettings role={role} settings={data.settings} onSaved={load} />
    </section>
  );
}

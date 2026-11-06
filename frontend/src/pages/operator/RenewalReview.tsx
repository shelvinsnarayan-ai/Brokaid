import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { Playbook, StaffHelp } from "../../components/ledger/StaffHelp";

const API = (import.meta as any).env.VITE_API_BASE || "";
const FLAG: Record<string, string> = {
  lapse_hold: "Lapse held (suspension)", refund_review: "Refund review", open_claim: "Open claim", open_complaint: "Open complaint",
};

interface Row {
  bucket: string; days: number; days_text: string; renewal_id: string | null; policy_id: string; client_name: string; policy_class: string;
  policy_number: string; insurer: string; expiry: string; stage: string; stage_label: string;
  ae: { user_id?: string | null; name: string; status: string }; money: null | { state: string; held_in_trust: boolean }; flags: string[];
  actions: { open: string | null; confirm_lapse: boolean; refund_or_bind: string | null; nudge: boolean };
}
interface Data { as_of: string; buckets: { key: string; label: string; count: number; rows: Row[] }[]; can_act: boolean; badge: number }

export default function RenewalReview() {
  const [data, setData] = useState<Data | null>(null);
  const [all, setAll] = useState<Row[]>([]);
  const [f, setF] = useState({ ae: "", stage: "", insurer: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const load = useCallback(() => {
    authCall<Data>(`o/renewal-review${qs ? `?${qs}` : ""}`, undefined, "GET").then(setData).catch((e) => setMsg(e.message));
  }, [qs]);
  useEffect(load, [load]);
  useEffect(() => {
    authCall<Data>("o/renewal-review", undefined, "GET").then((d) => setAll(d.buckets.flatMap((b) => b.rows))).catch(() => undefined);
  }, []);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null);
    try { await fn(); setMsg(ok); load(); } catch (e: any) { setMsg(e.message); }
  };
  const nudge = (r: Row) => act(() => authCall("o/renewal-review/nudge", r.renewal_id ? { renewal_id: r.renewal_id } : { policy_id: r.policy_id }), "AE nudged.");
  const lapse = (r: Row) => {
    const reason = window.prompt(`Reason for confirming the lapse of ${r.client_name} — ${r.policy_class} (recorded in the ledger):`) || "";
    if (reason.trim().length < 3) { setMsg("Enter a reason (at least 3 characters)."); return; }
    act(() => authCall(`o/renewals/${r.renewal_id}/confirm-lapse`, { reason }), "Lapse confirmed.");
  };
  const opts = (k: (r: Row) => [string, string]) => [...new Map(all.map((r) => k(r))).entries()];
  return (
    <section className="imp-page" data-testid="renewal-review-page">
      <p className="imp-eyebrow">Renewals · expiry review</p>
      <h1 className="imp-h1">Renewal review</h1>
      <Playbook k="renewal" />
      <p className="imp-muted">Calendar days to expiry, Sydney time, as of {data?.as_of || "…"}. Test data is excluded.
        {data && !data.can_act ? " Read-only for Principals." : ""}</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "16px 0" }}>
        <select value={f.ae} onChange={(e) => setF({ ...f, ae: e.target.value })} data-testid="review-filter-ae">
          <option value="">Any AE</option><option value="unassigned">Unassigned / AE disabled</option>
          {opts((r) => [r.ae.name || "", r.ae.name]).filter(([k]) => k).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={f.stage} onChange={(e) => setF({ ...f, stage: e.target.value })} data-testid="review-filter-stage">
          <option value="">Any stage</option>
          {opts((r) => [r.stage, r.stage_label]).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={f.insurer} onChange={(e) => setF({ ...f, insurer: e.target.value })} data-testid="review-filter-insurer">
          <option value="">Any insurer</option>
          {opts((r) => [r.insurer, r.insurer]).map(([k]) => <option key={k} value={k}>{k}</option>)}
        </select>
        <a className="k-btn" href={`${API}/api/o/renewal-review.csv${qs ? `?${qs}` : ""}`} data-testid="review-export-csv">Export CSV</a>
      </div>
      {msg ? <p className="imp-muted" role="status" data-testid="review-msg">{msg}</p> : null}
      {data?.buckets.map((b) => <Bucket key={b.key} b={b} canAct={data.can_act} onNudge={nudge} onLapse={lapse} />)}
    </section>
  );
}

function Bucket({ b, canAct, onNudge, onLapse }: { b: Data["buckets"][number]; canAct: boolean; onNudge: (r: Row) => void; onLapse: (r: Row) => void }) {
  return (
    <details open className="imp-card" style={{ marginBottom: 16 }} data-testid={`review-bucket-${b.key}`}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        {b.label} <span data-testid={`review-count-${b.key}`}>({b.count})</span>
      </summary>
      {b.rows.length ? (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger-table" style={{ width: "100%", marginTop: 8 }}>
            <thead><tr><th>Client</th><th>Policy</th><th>Insurer</th><th>Expiry</th><th>Days</th><th>Stage</th><th>AE</th><th>Money</th><th>Flags</th><th /></tr></thead>
            <tbody>{b.rows.map((r) => <ReviewRow key={r.renewal_id || r.policy_id} r={r} canAct={canAct} onNudge={onNudge} onLapse={onLapse} />)}</tbody>
          </table>
        </div>
      ) : <p className="imp-muted">Nothing here.</p>}
    </details>
  );
}

function ReviewRow({ r, canAct, onNudge, onLapse }: { r: Row; canAct: boolean; onNudge: (r: Row) => void; onLapse: (r: Row) => void }) {
  const id = r.renewal_id || r.policy_id;
  return (
    <tr data-testid={`review-row-${id}`}>
      <td>{r.client_name}</td>
      <td>{r.policy_class}<br /><span className="imp-muted">{r.policy_number}</span></td>
      <td>{r.insurer}</td>
      <td>{r.expiry}</td>
      <td data-testid={`review-days-${id}`} style={{ color: r.days < 0 ? "#E5484D" : undefined }}>{r.days_text}</td>
      <td>{r.stage_label}</td>
      <td style={{ color: r.ae.status === "ok" ? undefined : "#E5484D" }}>{r.ae.name}</td>
      <td>{r.money ? <>{r.money.state}{r.money.held_in_trust ? <><br /><span className="imp-warn">premium held in trust</span></> : null}</> : "—"}</td>
      <td>{r.flags.map((x) => FLAG[x]).join(", ") || "—"}</td>
      <td style={{ whiteSpace: "nowrap" }}>
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>
          {r.actions.open ? <Link to={r.actions.open} data-testid={`review-open-${id}`}>Open renewal</Link> : null}
          {r.actions.refund_or_bind ? <><Link to={r.actions.refund_or_bind} data-testid={`review-refund-${id}`}>Refund or bind</Link>
            <StaffHelp k="refund_or_bind" testid={`review-help-refund-${id}`} /></> : null}
          {canAct && r.actions.confirm_lapse ? <button className="k-btn" onClick={() => onLapse(r)} data-testid={`review-confirm-lapse-${id}`}>Confirm lapse</button> : null}
          {canAct && r.actions.confirm_lapse ? <StaffHelp k="lapse" testid={`review-help-lapse-${id}`} /> : null}
          {canAct ? <button className="k-btn" onClick={() => onNudge(r)} data-testid={`review-nudge-${id}`}>Nudge AE</button> : null}
        </span>
      </td>
    </tr>
  );
}

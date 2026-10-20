import { useCallback, useEffect, useState } from "react";
import { Basis } from "../stubKit";
import { Market } from "./Market";
import { Recommend } from "./Recommend";
import { TmdCheck } from "./TmdCheck";
import { OfflineSign } from "../OfflineSign";
import { api, aud, get, type Detail } from "./api";

export function RenewalDetail({ id, role, mode, onClose, onChanged }: { id: string; role?: string; mode?: string; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const load = useCallback(() => { get(`o/renewals/${id}`).then(setD).catch((e) => setErr(e.message)); }, [id]);
  useEffect(load, [load]);
  const act = async (action: string, body: unknown = {}) => {
    setErr(null);
    try { setD(await api(`o/renewals/${id}/${action}`, body)); onChanged(); } catch (e: any) { setErr(e.message); }
  };
  if (!d) return <section className="imp-card" data-testid="renewal-detail-loading">{err || "Loading…"}</section>;
  const r = d.renewal;
  const can = role === "ae" || role === "principal";
  return (
    <section className="imp-card rn-detail" data-testid="renewal-detail">
      <div className="imp-row" style={{ justifyContent: "space-between" }}>
        <h2 className="imp-h2" data-testid="renewal-detail-title">{r.client_name} · {r.product}</h2>
        <button className="k-btn" onClick={onClose} data-testid="renewal-detail-close">Close</button>
      </div>
      <p className="imp-muted" data-testid="renewal-detail-stage">
        Stage: <b className={`rn-stage rn-stage--${r.stage}`}>{r.stage_label}</b>{r.stage === "lapse_risk" ? ` (was ${r.current.replace("_", " ")})` : ""}
        {" · "}expiring {d.comparison.expiring.insurer} at {aud(d.comparison.expiring.premium)}, excess {aud(d.comparison.expiring.excess)}
      </p>
      <ol className="rn-history" data-testid="renewal-history">
        {r.history.map((h, i) => <li key={i}>{new Date(h.at).toLocaleDateString("en-AU")} — {h.to.replace(/_/g, " ")}{h.note ? ` (${h.note})` : ""}</li>)}
      </ol>
      {err ? <p className="imp-err" role="alert" data-testid="renewal-detail-error">{err}</p> : null}
      {can && r.current === "paid" ? <OfflineSign kind="renewal" refId={r.renewal_id} onDone={onChanged} /> : null}
      {can && r.current === "upcoming" ? (
        <button className="k-btn k-btn--primary" onClick={() => act("request-info")} data-testid="renewal-request-info">Send the client the renewal checklist</button>
      ) : null}
      {r.answers || r.issues.length ? (
        <div data-testid="renewal-info">
          <h3 className="imp-h3">Client details</h3>
          {r.accepted_changes.map((c, i) => <p key={i} className="imp-muted">Change: {c.operation} {c.field_path} {JSON.stringify(c.to_value || c.from_value)}</p>)}
          {r.issues.map((x, i) => <p key={i} className="imp-warn" data-testid={`renewal-issue-${i}`}>Needs review: {x}</p>)}
        </div>
      ) : null}
      {can && (["upcoming", "info_requested"].includes(r.current) || (r.current === "info_received" && r.needs_review)) ? (
        <div className="imp-row">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="How were the details confirmed?" data-testid="renewal-accept-note" />
          <button className="k-btn" onClick={() => act("accept-info", { note })} data-testid="renewal-accept-info">Accept details</button>
        </div>
      ) : null}
      {["info_received", "market", "quotes_in", "recommendation_ready"].includes(r.current) && !r.needs_review && can
        ? <Market id={id} quotes={r.quotes} act={act} mode={mode} /> : null}
      {d.comparison.options.length ? (
        <table className="imp-table" data-testid="renewal-comparison">
          <thead><tr><th>Insurer</th><th>Premium</th><th>vs expiring</th><th>Excess</th><th>Limits</th><th>Key differences</th></tr></thead>
          <tbody>{d.comparison.options.map((o) => (
            <tr key={o.quote_id} data-testid={`renewal-option-${o.quote_id}`} className={r.recommendation?.quote_id === o.quote_id ? "rn-row--rec" : ""}>
              <td>{o.insurer}<Basis b={o.data_basis} /></td><td className="rn-mono">{aud(o.premium)}</td>
              <td className="rn-mono">{Number(o.premium_change) >= 0 ? "+" : ""}{o.premium_change_pct}%</td>
              <td className="rn-mono">{aud(o.excess)}</td><td>{o.limits_same ? "Same" : "Different"}</td><td>{o.key_differences.join("; ")}</td>
            </tr>))}</tbody>
        </table>
      ) : null}
      {["quotes_in", "recommendation_ready"].includes(r.current) ? <TmdCheck r={r as any} role={role} act={act} /> : null}
      {["quotes_in", "recommendation_ready"].includes(r.current) ? <Recommend d={d} role={role} act={act} /> : null}
      <ComparisonEmails rows={(d as any).comparison_emails || []} canResend={can && ["client_decision", "paid"].includes(r.current)}
        onResend={() => act("resend-comparison")} />
      {d.payable ? <p className="imp-muted" data-testid="renewal-payable">Payment: CRN {d.payable.crn} · {aud(d.payable.amount)} · <b>{d.payable.state}</b></p> : null}
      {d.packet ? (
        <div data-testid="renewal-packet">
          <h3 className="imp-h3">Change packet for iAdviser (enter by hand — no write-back)</h3>
          <table className="imp-table"><tbody>{d.packet.lines.map((l, i) => <tr key={i}><th>{l.label}</th><td>{String(l.value ?? "—")}</td></tr>)}</tbody></table>
          <a className="k-btn" href={`${import.meta.env.VITE_API_BASE || ""}/api/o/renewals/${id}/change-packet.txt`} data-testid="renewal-packet-download">Download packet</a>
        </div>
      ) : null}
    </section>
  );
}

const SKIP: Record<string, string> = {
  "skipped:email_disabled": "email is off for this deployment", "skipped:pref_off": "the client turned document emails off",
  "skipped:example_address": "test address (.example)", "skipped:not_allowlisted": "address not on the allow-list",
  "skipped:no_address": "no email address on file", held: "held for quiet hours (sends 7:00 am Sydney)",
  pending: "queued", retry: "retrying", sending: "sending", failed: "failed after retries",
};

function ComparisonEmails({ rows, canResend, onResend }: { rows: any[]; canResend: boolean; onResend: () => void }) {
  if (!rows.length && !canResend) return null;
  const sent = rows.filter((x) => x.status === "sent");
  const when = (t: string) => new Date(t).toLocaleString("en-AU", { timeZone: "Australia/Sydney", dateStyle: "medium", timeStyle: "short" });
  return (
    <div data-testid="renewal-comparison-emails">
      {sent.length ? (
        <p className="imp-muted" data-testid="renewal-comparison-emailed">
          Comparison emailed to {[...new Set(sent.map((x) => x.name))].join(", ")} at {when(sent[0].sent_at)}
        </p>
      ) : null}
      {rows.filter((x) => x.status !== "sent").map((x, i) => (
        <p key={i} className="imp-muted" data-testid={`renewal-comparison-email-skip-${i}`}>
          Comparison email to {x.name}: {SKIP[x.status] || x.status.replace("skipped:", "not sent — ")}
        </p>
      ))}
      {canResend ? <button className="k-btn" onClick={onResend} data-testid="renewal-resend-comparison">Resend comparison email</button> : null}
    </div>
  );
}

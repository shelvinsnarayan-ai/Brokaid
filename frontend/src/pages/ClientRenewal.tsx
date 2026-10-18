/** R1.6 — client renewal: checklist → options + recommendation → choose → pay (CRN) → sign → renewed. */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { authCall } from "../lib/authApi";
import { RenewalChecklist } from "./renewal/Checklist";
import { AcceptPanel } from "../components/AcceptPanel";
import "../styles/renewals.css";

const aud = (v: string | number) => `$${Number(v).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function ClientRenewal() {
  const { renewal_id } = useParams();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { authCall(`c/renewals/${renewal_id}`, undefined, "GET").then(setD).catch((e) => setErr(e.message)); }, [renewal_id]);
  useEffect(load, [load]);
  const act = async (a: string, body: unknown = {}) => {
    setErr(null); setBusy(true);
    try { setD(await authCall(`c/renewals/${renewal_id}/${a}`, body)); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  if (!d) return <section className="brokaid-tab" data-testid="renewal-loading">{err || "Loading…"}</section>;
  const deciding = d.current === "client_decision" && !d.decision && d.options;
  return (
    <section className="brokaid-tab rn-c-page" data-testid="client-renewal">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Renewal · ends {d.expiry}</p>
        <h1 className="brokaid-tab__title">{d.product}</h1>
        <p className="brokaid-tab__lede" data-testid="client-renewal-text">{d.text}</p>
      </header>
      {err ? <p className="brokaid-form__err" role="alert" data-testid="client-renewal-error">{err}</p> : null}
      {d.checklist ? <RenewalChecklist items={d.checklist} busy={busy} onSubmit={(answers) => act("info", { answers })} /> : null}
      {d.recommendation ? (
        <div data-testid="client-renewal-recommendation">
          <h2>Your broker recommends</h2>
          <p>{d.recommendation.text}</p>
          <ul>{d.recommendation.reasons.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
          <p className="rn-c-warn" data-testid="client-renewal-gaw">{d.recommendation.general_advice_warning}</p>
        </div>
      ) : null}
      {d.options ? (
        <div className="rn-c-page" data-testid="client-renewal-options">
          <p>Your current cover: {d.expiring.insurer}, {aud(d.expiring.premium)} a year, excess {aud(d.expiring.excess)}.</p>
          {d.options.map((o: any) => (
            <label key={o.quote_id} className={`rn-c-option ${d.recommendation?.quote_id === o.quote_id ? "rn-c-option--rec" : ""}`}
              data-testid={`client-renewal-option-${o.quote_id}`}>
              <span>{deciding ? <input type="radio" name="opt" checked={choice === o.quote_id} onChange={() => setChoice(o.quote_id)}
                data-testid={`client-renewal-choose-${o.quote_id}`} /> : null} <b>{o.insurer}</b>
                {d.recommendation?.quote_id === o.quote_id ? " — recommended" : ""}{o.mocked ? " (example price)" : ""}</span>
              <span>{aud(o.premium)} a year ({Number(o.premium_change) >= 0 ? "up" : "down"} {aud(Math.abs(Number(o.premium_change)))}) · excess {aud(o.excess)}
                {o.limits_same ? " · same limits" : " · different limits"}</span>
              {o.key_differences.length ? <span className="rn-c-warn">{o.key_differences.join(" · ")}</span> : null}
            </label>
          ))}
          {deciding ? (
            <div className="brokaid-form__row">
              <button className="brokaid-cta" disabled={!choice || busy} onClick={() => act("decide", { choice })} data-testid="client-renewal-approve">
                I approve renewing with this option</button>
              <button className="k-btn" disabled={busy} onClick={() => act("decide", { choice: "decline" })} data-testid="client-renewal-decline">
                Don't renew</button>
            </div>
          ) : null}
        </div>
      ) : null}
      {d.payment && d.current === "client_decision" ? (
        <div data-testid="client-renewal-payment">
          <h2>Pay {aud(d.payment.amount)}</h2>
          {d.payment.bpay ? <p>BPAY biller code {d.payment.bpay.biller_code}, reference <b data-testid="client-renewal-crn">{d.payment.crn}</b></p> : null}
          {d.payment.eft ? <p>Or EFT to {d.payment.eft.account_name}, BSB {d.payment.eft.bsb}, account {d.payment.eft.account_number}, reference <b>{d.payment.crn}</b>
            {d.payment.mocked ? " (MOCKED details)" : ""}</p>
            : <p data-testid="client-renewal-pay-pending">{d.payment.message} Your reference will be <b data-testid="client-renewal-crn-pending">{d.payment.crn}</b>.</p>}
          {d.payment.state === "owed" ? <button className="brokaid-cta" disabled={busy} onClick={() => act("paid")} data-testid="client-renewal-paid">I've paid this</button>
            : <p data-testid="client-renewal-pay-state">Thanks — we'll confirm once the payment reaches the trust account (usually 1–2 business days).</p>}
        </div>
      ) : null}
      {d.current === "paid" || d.signature?.method ? <AcceptPanel kind="renewal" refId={d.renewal_id} onDone={load} /> : null}
      {d.new_policy_id ? (
        <Link className="brokaid-cta" to={`/c/cover/${d.new_policy_id}/certificate`} data-testid="client-renewal-certificate">See your new certificate →</Link>
      ) : null}
    </section>
  );
}

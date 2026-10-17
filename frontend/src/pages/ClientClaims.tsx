/** R1.8 — client claims: list (Claims tab), guided lodgement, detail. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { authCall } from "../lib/authApi";
import "../styles/phase13.css";
import "../styles/claims.css";
export { ClaimsNew } from "./claims/ClaimForm";
export { ClaimDetail } from "./claims/ClientClaimDetail";

interface Row { claim_id: string; claim_reference: string; stage: string; stage_label: string; status_text: string;
  claim_class: string; subtype: string; lodged_at: string; rego?: string | null }

export function ClientClaims() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { authCall<{ claims: Row[] }>("c/claims", undefined, "GET").then((d) => setRows(d.claims)).catch((e) => setErr(e.message)); }, []);
  return (
    <section className="brokaid-tab" data-testid="tab-claims-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Claims</p>
        <h1 className="brokaid-tab__title">Claims</h1>
        <Link to="/c/claims/new" className="brokaid-btn brokaid-btn--primary" data-testid="start-a-claim">Make a claim</Link>
      </header>
      {err ? <p className="brokaid-error" data-testid="claims-error">Couldn't load claims.</p> : null}
      {rows && rows.length === 0 ? <EmptyState title="No claims" body="Make a claim here. Your broker reviews it within one business day." testId="claims-empty" /> : null}
      {rows && rows.length > 0 ? (
        <ul className="brokaid-claims-list" data-testid="claims-list">
          {rows.map((c) => (
            <li key={c.claim_id}>
              <Link to={`/c/claims/${c.claim_id}`} className="brokaid-claim-card" data-testid={`claim-card-${c.claim_reference}`}>
                <div className="brokaid-claim-card__row-top">
                  <span className="brokaid-claim-card__ref">{c.claim_reference}</span>
                  <span className={`clm-stage clm-stage--${c.stage}`} data-testid={`claim-status-${c.stage}`}>{c.stage_label}</span>
                </div>
                <p className="brokaid-claim-card__preview">{c.status_text}</p>
                <p className="brokaid-claim-card__ts">{c.claim_class} {c.subtype ? `· ${c.subtype}` : ""}{c.rego ? ` · ${c.rego}` : ""} · lodged {c.lodged_at ? new Date(c.lodged_at).toLocaleDateString("en-AU") : "—"}</p>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

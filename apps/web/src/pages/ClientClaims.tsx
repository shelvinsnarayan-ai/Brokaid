/**
 * Claims tab + placeholder /c/claims/new scaffold.
 */
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";

export function ClientClaims() {
  return (
    <section className="brokaid-tab" data-testid="tab-claims-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Claims</p>
        <h1 className="brokaid-tab__title">Claims</h1>
      </header>

      <EmptyState
        title="No open claims"
        body="Report an incident here. Your broker sees it the same second."
        testId="claims-empty"
        cta={
          <Link
            to="/c/claims/new"
            className="brokaid-btn brokaid-btn--primary"
            data-testid="start-a-claim"
          >
            Start a claim
          </Link>
        }
      />
    </section>
  );
}

export function ClaimsNew() {
  return (
    <section className="brokaid-tab" data-testid="claims-new-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Claims / New</p>
        <h1 className="brokaid-tab__title">New claim</h1>
      </header>
      <EmptyState
        title="Claim flow lands in the claim scene phase"
        body="This scaffold is here so navigation feels complete. The
              full FNOL flow (photos, location, timeline, forwarding to
              carrier) comes later."
        testId="claims-new-placeholder"
        cta={
          <Link
            to="/c/claims"
            className="brokaid-btn brokaid-btn--secondary"
            data-testid="claims-new-back"
          >
            Back to claims
          </Link>
        }
      />
    </section>
  );
}

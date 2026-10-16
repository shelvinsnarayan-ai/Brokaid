/**
 * ClientPendingQuote — /c/cover/:policy_id/pending
 *
 * Shows the mocked carrier quote card whenever an EndorsementQuoteReady
 * exists for the given policy. Renders MOCKED · CARRIER badge prominently.
 * Buttons are scaffolds; approvals + payment land in phase 11, ask
 * lands in phase 12.
 */
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { useAuthority, usePack } from "../state/meStore";
import { fmtAud } from "../lib/format";

const API = import.meta.env.VITE_API_BASE || "";

interface PendingQuote {
  pending: boolean;
  state?: "with_broker" | "quote_ready" | "awaiting_broker_review" | "declined";
  envelope_id?: string;
  policy_id?: string;
  submission_summary?: string;
  structured_submission?: {
    cover_type?: string;
    market_targets?: string[];
    requested_changes?: any[];
  };
  carrier?: {
    carrier_name: string;
    premium_change_amount: string;
    premium_new_annual: string;
    premium_currency: string;
    terms: string;
    conditions: string[];
    quoted_at: string;
    mocked: true;
  };
  reason?: string;
}

function formatMoney(amount: string, _currency: string): string {
  return fmtAud(amount, { signed: true });
}

export function ClientPendingQuote() {
  const { policy_id = "" } = useParams();
  const navigate = useNavigate();
  const pack = usePack();
  const auth = useAuthority();
  const [data, setData] = useState<PendingQuote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [approveErr, setApproveErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/api/c/policies/${policy_id}/pending-quote`, {
          credentials: "include",
        });
        if (!r.ok) { setError(`${r.status}`); return; }
        setData(await r.json());
      } catch (e) { setError(String(e)); }
    })();
  }, [policy_id]);

  async function approveAndPay() {
    if (!data?.envelope_id) return;
    setApproving(true);
    setApproveErr(null);
    try {
      const r = await fetch(
        `${API}/api/c/matters/${policy_id}/approve/${data.envelope_id}`,
        { method: "POST", credentials: "include" }
      );
      if (!r.ok) {
        setApproveErr(`${r.status}: ${await r.text()}`);
        setApproving(false);
        return;
      }
      navigate(`/c/cover/${policy_id}/pay/${data.envelope_id}`);
    } catch (e: any) {
      setApproveErr(String(e));
      setApproving(false);
    }
  }

  if (error) return (
    <section className="brokaid-tab" data-testid="pending-quote-error">
      <EmptyState title="We couldn't load that quote" body={`Error ${error}`} />
    </section>
  );
  if (!data) return (
    <section className="brokaid-tab" data-testid="pending-quote-loading">
      <EmptyState title="Loading…" body="" />
    </section>
  );
  if (!data.pending) return (
    <section className="brokaid-tab" data-testid="pending-quote-none">
      <EmptyState
        title="No pending quote"
        body={data.reason === "already_executed" ? "This change has already been executed." : "There's nothing waiting for you on this policy right now."}
      />
      <Link to="/c/cover" className="brokaid-btn brokaid-btn--secondary">← Back to cover</Link>
    </section>
  );
  if (data.state === "declined") return (
    <section className="brokaid-tab" data-testid="pending-quote-declined">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Contact your broker</p>
        <h1 className="brokaid-tab__title">We can't handle this one here</h1>
        <p className="brokaid-tab__lede">
          This isn't something we can handle through the portal — please contact your broker directly.
        </p>
      </header>
      <Link to="/c/cover" className="brokaid-btn brokaid-btn--secondary">← Back to cover</Link>
    </section>
  );
  if ((data as any).state === "broker_reviewed") return (
    <section className="brokaid-tab" data-testid="pending-quote-broker-reviewed">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">With your broker</p>
        <h1 className="brokaid-tab__title">Your broker has reviewed this change</h1>
        <p className="brokaid-tab__lede">
          Your broker has picked this up and will contact you about the next step.
        </p>
      </header>
      <Link to="/c/cover" className="brokaid-btn brokaid-btn--secondary">← Back to cover</Link>
    </section>
  );
  if (data.state === "awaiting_broker_review") return (
    <section className="brokaid-tab" data-testid="pending-quote-broker-review">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">With your broker</p>
        <h1 className="brokaid-tab__title">Your broker is reviewing this change</h1>
        <p className="brokaid-tab__lede">
          Some details in your request don't match what we hold on this
          policy, so your broker will check them with you before anything
          is quoted.
        </p>
      </header>
      <div className="brokaid-quote-card" data-testid="broker-review-card">
        <p className="brokaid-quote-card__summary">
          No action needed right now. Your broker will be in touch.
        </p>
      </div>
      <Link to="/c/cover" className="brokaid-btn brokaid-btn--secondary">← Back to cover</Link>
    </section>
  );
  if (data.state === "with_broker" || (data as any).state === "with_broker") return (
    <section className="brokaid-tab" data-testid="pending-quote-with-broker">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">With your broker</p>
        <h1 className="brokaid-tab__title">We're on it — usually under an hour.</h1>
        <p className="brokaid-tab__lede">
          Your request has landed with your broker. They'll get you a quote
          from the market and you'll see it here as soon as it's ready.
        </p>
      </header>
      <div className="brokaid-quote-card" data-testid="with-broker-card">
        <div className="brokaid-quote-card__head">
          <div>
            <p className="brokaid-quote-card__label">Status</p>
            <h2 className="brokaid-quote-card__cover">Awaiting broker approval</h2>
          </div>
        </div>
        <p className="brokaid-quote-card__summary">
          No action needed from you right now. We'll notify you when the
          quote is ready to approve. Typical turnaround: under an hour in
          business hours.
        </p>
      </div>
      {pack && (
        <footer className="brokaid-disclosure">
          <strong>Duty of disclosure.</strong>{" "}
          {pack.disclosure_strings.duty_of_disclosure}
        </footer>
      )}
      <Link to="/c/cover" className="brokaid-btn brokaid-btn--secondary">← Back to cover</Link>
    </section>
  );

  const c = data.carrier!;
  const cover = data.structured_submission?.cover_type;
  const markets = data.structured_submission?.market_targets || [];

  return (
    <section className="brokaid-tab" data-testid="pending-quote-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Pending quote</p>
        <h1 className="brokaid-tab__title">Ready for your review</h1>
        <p className="brokaid-tab__lede">
          Your broker drafted a change and got a quote from the market. Take a
          look, and let them know if you're happy to proceed.
        </p>
      </header>

      <article className="brokaid-quote-card" data-testid="quote-card">
        <div className="brokaid-quote-card__head">
          <div>
            <p className="brokaid-quote-card__label">Cover</p>
            <h2 className="brokaid-quote-card__cover" data-testid="quote-cover-type">
              {cover || "—"}
            </h2>
          </div>
        </div>

        <p className="brokaid-quote-card__summary" data-testid="quote-summary">
          {data.submission_summary || "Your broker prepared a submission for market."}
        </p>

        <div className="brokaid-quote-card__grid">
          <div>
            <p className="brokaid-quote-card__stat-label">Carrier</p>
            <p className="brokaid-quote-card__stat-value" data-testid="quote-carrier">{c.carrier_name}</p>
          </div>
          <div>
            <p className="brokaid-quote-card__stat-label">Premium change</p>
            <p className="brokaid-quote-card__stat-value brokaid-quote-card__stat-value--emphasis" data-testid="quote-change">
              {formatMoney(c.premium_change_amount, c.premium_currency)}
            </p>
          </div>
          <div>
            <p className="brokaid-quote-card__stat-label">New annual premium</p>
            <p className="brokaid-quote-card__stat-value" data-testid="quote-new-annual">
              {fmtAud(c.premium_new_annual)}
            </p>
          </div>
        </div>

        <div className="brokaid-quote-card__terms">
          <p className="brokaid-quote-card__stat-label">Carrier terms</p>
          <p data-testid="quote-terms">{c.terms}</p>
          {c.conditions.length > 0 && (
            <ul className="brokaid-quote-card__conditions" data-testid="quote-conditions">
              {c.conditions.map((cond) => <li key={cond}>{cond}</li>)}
            </ul>
          )}
        </div>

        {markets.length > 0 && (
          <div className="brokaid-quote-card__markets" data-testid="quote-markets">
            <p className="brokaid-quote-card__stat-label">Broker approached</p>
            <p>{markets.join(" · ")}</p>
          </div>
        )}

        <div className="brokaid-quote-card__actions">
          <button
            type="button"
            className="brokaid-btn brokaid-btn--primary"
            onClick={approveAndPay}
            disabled={approving}
            aria-disabled={!auth.canApprove}
            title={auth.canApprove ? undefined : auth.hint}
            style={auth.canApprove ? undefined : { opacity: 0.45, cursor: "not-allowed" }}
            data-testid="quote-approve"
          >
            {approving ? "Approving…" : "Approve and pay"}
          </button>
          <Link
            to={`/c/ask?quote_id=${c && (data.envelope_id || "")}`}
            className="brokaid-btn brokaid-btn--secondary"
            data-testid="quote-ask"
          >
            Ask a question
          </Link>
        </div>
        {!auth.canApprove && (
          <p className="brokaid-quote-card__summary" style={{ opacity: 0.75, fontSize: 13 }} data-testid="quote-owner-only-hint">
            {auth.hint}
          </p>
        )}
        {approveErr && (
          <p
            className="brokaid-quote-card__summary"
            data-testid="quote-approve-error"
          >
            {approveErr}
          </p>
        )}
      </article>

      {pack && (
        <footer className="brokaid-disclosure" data-testid="pending-quote-disclosure">
          <strong>Duty of disclosure.</strong>{" "}
          {pack.disclosure_strings.duty_of_disclosure}
        </footer>
      )}
    </section>
  );
}

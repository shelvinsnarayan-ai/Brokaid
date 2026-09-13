/**
 * ClientPendingQuote — /c/cover/:policy_id/pending
 *
 * Shows the mocked carrier quote card whenever an EndorsementQuoteReady
 * exists for the given policy. Renders MOCKED · CARRIER badge prominently.
 * Buttons are scaffolds; approvals + payment land in phase 11, ask
 * lands in phase 12.
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { EmptyState } from "../components/EmptyState";
import { usePack } from "../state/meStore";

const API = import.meta.env.VITE_API_BASE || "";

interface PendingQuote {
  pending: boolean;
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

function formatMoney(amount: string, currency: string): string {
  const n = Number(amount);
  const sign = n > 0 ? "+" : "";
  return `${sign}${currency} ${new Intl.NumberFormat("en-AU", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(n)}`;
}

export function ClientPendingQuote() {
  const { policy_id = "" } = useParams();
  const pack = usePack();
  const [data, setData] = useState<PendingQuote | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          <MockedBadge label="carrier" variant="client" testId="quote-mocked-badge" />
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
              {c.premium_currency} {new Intl.NumberFormat("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(c.premium_new_annual))}
            </p>
          </div>
        </div>

        <div className="brokaid-quote-card__terms">
          <p className="brokaid-quote-card__stat-label">Carrier terms</p>
          <p data-testid="quote-terms">{c.terms}</p>
          {c.conditions.length > 0 && (
            <ul className="brokaid-quote-card__conditions" data-testid="quote-conditions">
              {c.conditions.map((cond, i) => <li key={i}>{cond}</li>)}
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
            disabled
            title="Approve + pay will land with the payment step in the next phase"
            data-testid="quote-approve-scaffold"
          >
            Approve and pay
          </button>
          <Link
            to={`/c/ask?quote_id=${c && (data.envelope_id || "")}`}
            className="brokaid-btn brokaid-btn--secondary"
            data-testid="quote-ask-scaffold"
          >
            Ask a question
          </Link>
        </div>
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

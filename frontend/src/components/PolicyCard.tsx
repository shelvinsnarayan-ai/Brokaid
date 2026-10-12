/**
 * PolicyCard — Managed vs Tracked visual language.
 *   - Managed: Harbour+Sea accent, "Managed by Harbourline".
 *   - Tracked: muted, Slate accent, dashed border, "Tracked policy —
 *     held elsewhere".
 * `mocked_insurer` renders a `MockedBadge label="carrier"` (rule 5).
 */
import { AmountDisplay } from "./AmountDisplay";

export interface Policy {
  policy_id: string;
  status: "managed" | "tracked" | "pending" | "lapsed";
  product: string;
  insurer_display: string;
  premium: { amount: number; currency: string; symbol: string };
  imported_from: string | null;
  mocked_insurer: boolean;
  schedule?: any;
  inception?: string;
  expiry?: string;
}

function daysUntil(iso: string | undefined): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  return Math.max(0, Math.round(ms / (1000 * 60 * 60 * 24)));
}

export function PolicyCard({ policy }: { policy: Policy }) {
  const isTracked = policy.status === "tracked";
  const renewalDays = daysUntil(policy.expiry);
  const micro = isTracked
    ? "Tracked policy — held elsewhere"
    : "Managed by Harbourline";
  return (
    <article
      className={`brokaid-policy-card brokaid-policy-card--${policy.status}`}
      data-testid={`policy-card-${policy.status}`}
      aria-label={`${policy.product} policy, ${policy.status}`}
    >
      <header className="brokaid-policy-card__head">
        <span
          className={`brokaid-chip brokaid-chip--${policy.status}`}
          data-testid={`policy-chip-${policy.status}`}
        >
          {policy.status}
        </span>
      </header>
      <h3 className="brokaid-policy-card__title">{policy.product}</h3>
      <div className="brokaid-policy-card__micro">{micro}</div>

      <dl className="brokaid-policy-card__grid">
        <div>
          <dt>Insurer</dt>
          <dd>{policy.insurer_display}</dd>
        </div>
        <div>
          <dt>Premium</dt>
          <dd>
            <AmountDisplay
              amount={policy.premium.amount}
              testId={`policy-premium-${policy.status}`}
            />
          </dd>
        </div>
        {renewalDays !== null && (
          <div>
            <dt>Renews</dt>
            <dd>{renewalDays} days</dd>
          </div>
        )}
      </dl>
    </article>
  );
}

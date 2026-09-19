/*! brokaid:dev-only */
/**
 * Kitchen-sink route — renders every phase-6 primitive so we can
 * eyeball / visually regress the design system. Only mounted when
 * AUTH_DEV_MODE=true.
 */
import { MockedBadge } from "@brokaid/mocks";
import { AmountDisplay } from "../components/AmountDisplay";
import { EmptyState } from "../components/EmptyState";
import { PolicyCard } from "../components/PolicyCard";

export function KitchenSink() {
  const managed = {
    policy_id: "ks-managed",
    status: "managed" as const,
    product: "Marine Liability",
    insurer_display: "Fictional Carrier A",
    premium: { amount: 8500, currency: "AUD", symbol: "$" },
    imported_from: null,
    mocked_insurer: true,
    schedule: {},
    inception: new Date().toISOString(),
    expiry: new Date(Date.now() + 200 * 86400_000).toISOString(),
  };
  const tracked = {
    ...managed,
    policy_id: "ks-tracked",
    status: "tracked" as const,
    product: "Cyber",
    insurer_display: "Fictional Carrier B",
    premium: { amount: 2200, currency: "AUD", symbol: "$" },
    imported_from: "manual_entry_burmac",
  };
  return (
    <section className="brokaid-tab" data-testid="kitchen-sink-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">/c/_kitchen-sink</p>
        <h1 className="brokaid-tab__title">Design tokens</h1>
      </header>

      <div className="brokaid-ks-row">
        <span className="brokaid-chip brokaid-chip--managed">managed</span>
        <span className="brokaid-chip brokaid-chip--tracked">tracked</span>
        <span className="brokaid-chip brokaid-chip--muted">chip muted</span>
        <MockedBadge label="carrier" variant="client" />
      </div>

      <div className="brokaid-ks-row">
        <button className="brokaid-btn brokaid-btn--primary">Primary</button>
        <button className="brokaid-btn brokaid-btn--secondary">Secondary</button>
        <button className="brokaid-btn brokaid-btn--secondary" disabled>
          Disabled
        </button>
      </div>

      <div className="brokaid-ks-row">
        <AmountDisplay amount={1234.56} fractionDigits={2} />
      </div>

      <EmptyState title="Empty state" body="Tuned to the Harbour palette." />

      <div className="brokaid-cards">
        <PolicyCard policy={managed} />
        <PolicyCard policy={tracked} />
      </div>
    </section>
  );
}

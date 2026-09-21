/**
 * Money tab — MOCKED banner (finance surface hasn't shipped yet).
 */
import { MockedBadge } from "@brokaid/mocks";
import { EmptyState } from "../components/EmptyState";
import { usePack } from "../state/meStore";

export function ClientMoney() {
  const pack = usePack();
  return (
    <section className="brokaid-tab" data-testid="tab-money-root">
      <header className="brokaid-tab__head brokaid-tab__head--mocked">
        <p className="brokaid-tab__eyebrow">
          Money <MockedBadge label="payment" variant="client" />
        </p>
        <h1 className="brokaid-tab__title">Invoices & statements</h1>
      </header>

      <EmptyState
        title="Nothing to pay right now"
        body="Your invoices, statements and scheduled payments will land here once your broker enables Money on this account."
        testId="money-empty"
      />

      {pack && (
        <p className="brokaid-tab__note" data-testid="money-rail">
          Payments via <strong>{pack.rail_label}</strong>.
        </p>
      )}
    </section>
  );
}

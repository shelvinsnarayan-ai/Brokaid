/**
 * Money tab — R1.5: what you owe, what you've told us you paid, and what the bank has confirmed.
 */
import { useEffect, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { authCall } from "../lib/authApi";
import { usePack } from "../state/meStore";

interface Item { payable_id: string; crn: string; amount: string; currency: string; state: string; due_date: string; product?: string; received: string; policy_id: string; envelope_id: string }
const STATE: Record<string, { label: string; note: string }> = {
  owed: { label: "Owed", note: "Not paid yet" },
  advised: { label: "Advised", note: "You told us you've paid. Waiting for the bank to confirm." },
  partial: { label: "Part received", note: "The bank has confirmed part of this payment." },
  received: { label: "Received", note: "Your broker's bank has confirmed this payment." },
  overpaid: { label: "Received (more than owed)", note: "We received more than was owed. Your broker will be in touch." },
};

export function ClientMoney() {
  const pack = usePack();
  const [items, setItems] = useState<Item[] | null>(null);
  useEffect(() => { authCall<{ items: Item[] }>("c/money", undefined, "GET").then((d) => setItems(d.items)).catch(() => setItems([])); }, []);
  return (
    <section className="brokaid-tab" data-testid="tab-money-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Money</p>
        <h1 className="brokaid-tab__title">Payments</h1>
      </header>
      {items && items.length === 0 ? (
        <EmptyState title="Nothing to pay right now" body="Amounts you owe for changes to your cover will show here, with your payment reference." testId="money-empty" />
      ) : (
        <ul className="brokaid-money" data-testid="money-list">
          {(items ?? []).map((i) => (
            <li key={i.payable_id} className={`brokaid-money__item brokaid-money__item--${i.state}`} data-testid={`money-item-${i.crn}`}>
              <div>
                <strong>{i.product ?? "Policy change"}</strong>
                <span className="brokaid-money__ref">Reference (CRN) <code data-testid={`money-crn-${i.crn}`}>{i.crn}</code></span>
              </div>
              <div className="brokaid-money__amt">${i.amount}</div>
              <div>
                <span className={`brokaid-money__chip brokaid-money__chip--${i.state}`} data-testid={`money-state-${i.crn}`}>{STATE[i.state]?.label ?? i.state}</span>
                <p className="brokaid-money__note">{STATE[i.state]?.note}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      {pack && <p className="brokaid-tab__note" data-testid="money-rail">Payments via <strong>{pack.rail_label}</strong>.</p>}
    </section>
  );
}

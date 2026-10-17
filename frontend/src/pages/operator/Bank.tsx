import { useState } from "react";
import { useOperatorStore } from "../../state/operatorStore";
import { Lines } from "./bank/Lines";
import { PaySettings } from "./bank/PaySettings";
import { Statements } from "./bank/Statements";
import { Owed, Recon, Trust } from "./bank/Views";
import "../../styles/import.css";

const TABS = [["lines", "Statement lines"], ["statements", "Statements"], ["owed", "Money owed"], ["trust", "Trust record"], ["recon", "Reconciliation"], ["settings", "Payment details"]] as const;

export function BankPage() {
  const role = useOperatorStore((s) => s.session)?.session?.active_role;
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("lines");
  const [tick, setTick] = useState(0);
  const bump = () => setTick((t) => t + 1);
  if (role && !["operator", "principal"].includes(role)) return <p data-testid="bank-forbidden">Only the Operator or a Principal can open the bank feed.</p>;
  return (
    <section className="ledger-page imp-page" data-testid="bank-page">
      <h1 className="ledger-page__title">Bank feed &amp; trust account</h1>
      <p className="imp-muted">Money is Owed, then Advised (the client says they paid), then Received (a bank statement line confirms it). Only a bank line makes a payment Received.</p>
      <div className="imp-tabs">{TABS.map(([k, l]) => <button key={k} className={`k-btn ${tab === k ? "k-btn--primary" : ""}`} onClick={() => setTab(k)} data-testid={`bank-tab-${k}`}>{l}</button>)}</div>
      {tab === "statements" && <Statements onImported={bump} />}
      {tab === "lines" && <Lines tick={tick} changed={bump} />}
      {tab === "owed" && <Owed tick={tick} />}
      {tab === "trust" && <Trust tick={tick} changed={bump} />}
      {tab === "recon" && <Recon tick={tick} role={role} changed={bump} />}
      {tab === "settings" && <PaySettings role={role} />}
    </section>
  );
}

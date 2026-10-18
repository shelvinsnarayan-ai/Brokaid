/** R1.7 — /o/compliance: disclosure documents, client type + legal hold, complaints (IDR), privacy, retention, settings. */
import { useState } from "react";
import { useOperatorStore } from "../../state/operatorStore";
import { Complaints } from "./compliance/Complaints";
import { Docs } from "./compliance/Docs";
import { Library } from "./compliance/Library";
import { ClientsTab, Retention } from "./compliance/Records";
import { Privacy } from "./compliance/Privacy";
import { Settings } from "./compliance/Settings";
import "../../styles/import.css";
import "../../styles/renewals.css";

const TABS = [["complaints", "Complaints"], ["library", "Documents"], ["clients", "Clients"], ["privacy", "Privacy"],
  ["retention", "Retention"], ["settings", "Settings"]] as const;

export function CompliancePage() {
  const role = useOperatorStore((s) => s.session)?.session?.active_role;
  const [tab, setTab] = useState<string>("complaints");
  return (
    <section className="ledger-page imp-page" data-testid="compliance-page">
      <header>
        <p className="imp-muted">COMPLIANCE · RECORDS AND WORKFLOWS</p>
        <h1 className="ledger-page__title">Compliance</h1>
        <p className="imp-muted">These records support the brokerage's obligations. They don't make anything legally compliant on their own.</p>
      </header>
      <div className="rn-stages">
        {TABS.map(([k, l]) => (
          <button key={k} className={`rn-chip ${tab === k ? "rn-chip--on" : ""}`} onClick={() => setTab(k)} data-testid={`compliance-tab-${k}`}>{l}</button>
        ))}
      </div>
      {tab === "complaints" ? <Complaints role={role} /> : null}
      {tab === "library" ? <><Library role={role} /><Docs role={role} /></> : null}
      {tab === "clients" ? <ClientsTab role={role} /> : null}
      {tab === "privacy" ? <Privacy role={role} /> : null}
      {tab === "retention" ? <Retention role={role} /> : null}
      {tab === "settings" ? <Settings role={role} /> : null}
    </section>
  );
}

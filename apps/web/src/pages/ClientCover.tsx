/**
 * Cover tab — full list of policies with schedule summary + renewal.
 */
import { useEffect, useState } from "react";
import { PolicyCard, type Policy } from "../components/PolicyCard";
import { EmptyState } from "../components/EmptyState";
import { useMeStore, usePack } from "../state/meStore";

const API = import.meta.env.VITE_API_BASE || "";

export function ClientCover() {
  const me = useMeStore((s) => s.me);
  const pack = usePack();
  const [policies, setPolicies] = useState<Policy[]>([]);

  useEffect(() => {
    (async () => {
      const res = await fetch(`${API}/api/c/policies`, {
        credentials: "include",
      });
      if (res.ok) setPolicies((await res.json()).policies);
    })();
  }, [me?.client_prefs?.jurisdiction]);

  return (
    <section className="brokaid-tab" data-testid="tab-cover-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Cover</p>
        <h1 className="brokaid-tab__title">Your cover</h1>
        <p className="brokaid-tab__lede">
          Managed policies are placed and serviced by BurMac. Tracked
          policies are held elsewhere and recorded here for completeness.
        </p>
      </header>

      {policies.length === 0 && (
        <EmptyState
          title="No cover in view yet"
          body="Your broker hasn't attached any cover to this account."
          testId="cover-empty"
        />
      )}

      <section className="brokaid-cards" data-testid="cover-policy-cards">
        {policies.map((p) => (
          <div key={p.policy_id} className="brokaid-cover-row">
            <PolicyCard policy={p} />
            <div className="brokaid-cover-row__schedule">
              {p.schedule?.cover_types?.map((c: any, i: number) => (
                <span key={i} className="brokaid-chip brokaid-chip--muted">
                  {c.type}
                </span>
              ))}
            </div>
            <button
              type="button"
              className="brokaid-btn brokaid-btn--secondary"
              disabled
              title="Coming in the endorsement flow"
              data-testid={`request-change-${p.status}`}
            >
              Request change
            </button>
          </div>
        ))}
      </section>

      {pack && (
        <footer className="brokaid-disclosure" data-testid="cover-disclosure">
          <strong>Duty of disclosure.</strong>{" "}
          {pack.disclosure_strings.duty_of_disclosure}
        </footer>
      )}
    </section>
  );
}

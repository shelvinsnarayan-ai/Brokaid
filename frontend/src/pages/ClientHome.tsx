/**
 * Home tab — greeting + Next action + two policy cards + Ask CTA.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PolicyCard, type Policy } from "../components/PolicyCard";
import { useMeStore, usePack } from "../state/meStore";

const API = import.meta.env.VITE_API_BASE || "";

interface NextAction {
  kind: string;
  policy_id: string | null;
  summary: string | null;
  revised_premium_aud: number | null;
  delta_aud: number | null;
  href: string | null;
  mocked: boolean;
}

export function ClientHome() {
  const me = useMeStore((s) => s.me);
  const pack = usePack();
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [nextAction, setNextAction] = useState<NextAction | null>(null);

  useEffect(() => {
    (async () => {
      const res = await fetch(`${API}/api/c/policies`, {
        credentials: "include",
      });
      if (res.ok) setPolicies((await res.json()).policies);
    })();
    (async () => {
      const res = await fetch(`${API}/api/c/next-action`, {
        credentials: "include",
      });
      if (res.ok) setNextAction((await res.json()).next_action);
    })();
  }, [me?.client_prefs?.jurisdiction]);

  return (
    <section className="brokaid-tab brokaid-tab--home" data-testid="tab-home-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Home</p>
        <h1 className="brokaid-tab__title">
          Hi {me?.user?.display_name?.split(" ")[0] ?? "there"}.
        </h1>
        {me?.linked_client && (
          <p className="brokaid-tab__lede">
            {me.linked_client.display_name} · {me.linked_client.domicile}
          </p>
        )}
      </header>

      {nextAction && nextAction.href ? (
        <Link
          to={nextAction.href}
          className="brokaid-next-action"
          data-testid="home-next-action-card"
        >
          <span className="brokaid-next-action__eyebrow">
            You have 1 endorsement to approve
          </span>
          <span className="brokaid-next-action__body">
            {nextAction.summary}
          </span>
          {nextAction.revised_premium_aud != null && (
            <span className="brokaid-next-action__meta">
              Revised premium AUD ${nextAction.revised_premium_aud.toLocaleString()}
              {nextAction.delta_aud != null && (
                <> · +AUD ${nextAction.delta_aud.toLocaleString()}</>
              )}
            </span>
          )}
          <span className="brokaid-next-action__cta">Review and approve →</span>
        </Link>
      ) : (
        <EmptyState
          title="No next action"
          body="When your broker needs you or something is due, you'll see it here."
          testId="home-next-action"
        />
      )}

      <section className="brokaid-cards" data-testid="home-policy-cards">
        {policies.map((p) => (
          <PolicyCard key={p.policy_id} policy={p} />
        ))}
      </section>

      <Link
        to="/c/ask"
        className="brokaid-cta"
        data-testid="home-ask-cta"
      >
        Ask your broker →
      </Link>

      {pack && (
        <footer className="brokaid-disclosure" data-testid="home-disclosure">
          <strong>Privacy notice.</strong>{" "}
          {pack.disclosure_strings.privacy_notice}
        </footer>
      )}
    </section>
  );
}

/**
 * Home tab — greeting + Next action + two policy cards + Ask CTA.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PolicyCard, type Policy } from "../components/PolicyCard";
import { useMeStore, usePack } from "../state/meStore";

const API = import.meta.env.VITE_API_BASE || "";

export function ClientHome() {
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

      <EmptyState
        title="No next action"
        body="When your broker needs you or something is due, you'll see it here."
        testId="home-next-action"
      />

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

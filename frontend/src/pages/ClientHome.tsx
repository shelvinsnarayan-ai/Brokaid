/**
 * Home tab — greeting + Next action + two policy cards + Ask CTA.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { RenewalCards } from "../components/RenewalCard";
import { ThingGroups, type Thing } from "../components/ThingCard";
import { useMeStore, usePack } from "../state/meStore";
import { fmtAud } from "../lib/format";

const API = import.meta.env.VITE_API_BASE || "";

interface NextAction {
  kind: string;
  policy_id: string | null;
  envelope_id?: string | null;
  certificate_id?: string | null;
  summary: string | null;
  revised_premium_aud: number | null;
  delta_aud: number | null;
  href: string | null;
  mocked: boolean;
}

export function ClientHome() {
  const me = useMeStore((s) => s.me);
  const pack = usePack();
  const [things, setThings] = useState<Thing[]>([]);
  const [nextAction, setNextAction] = useState<NextAction | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await fetch(`${API}/api/c/things`, {
        credentials: "include",
      });
      if (!cancelled && res.ok) setThings((await res.json()).things);
    })();
    async function loadNextAction() {
      const res = await fetch(`${API}/api/c/next-action`, {
        credentials: "include",
      });
      if (!cancelled && res.ok)
        setNextAction((await res.json()).next_action);
    }
    loadNextAction();
    // Poll every 4s so the CTA surfaces within seconds of the mocked
    // carrier returning a quote (Build Brief §8 R1 #2 — "Home CTA
    // leads to the pending quote").
    const t = window.setInterval(loadNextAction, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
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
            {nextAction.kind === "certificate_ready"
              ? "New certificate ready"
              : nextAction.kind === "with_broker"
              ? "With your broker"
              : "Quote ready — review and approve"}
          </span>
          <span className="brokaid-next-action__body">
            {nextAction.summary}
          </span>
          {nextAction.revised_premium_aud != null && (
            <span className="brokaid-next-action__meta">
              Revised premium {fmtAud(nextAction.revised_premium_aud)}
              {nextAction.delta_aud != null && (
                <> · {fmtAud(nextAction.delta_aud, { signed: true })}</>
              )}
            </span>
          )}
          <span className="brokaid-next-action__cta">
            {nextAction.kind === "certificate_ready"
              ? "Open certificate →"
              : nextAction.kind === "with_broker"
              ? "See status →"
              : "Review and approve →"}
          </span>
        </Link>
      ) : (
        <EmptyState
          title="No next action"
          body="When your broker needs you or something is due, you'll see it here."
          testId="home-next-action"
        />
      )}

      <RenewalCards where="home" />

      <ThingGroups things={things} />

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

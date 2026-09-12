/**
 * Client home — the two policies as cards, currency + disclosure driven
 * by the active jurisdiction pack. Dev-only jurisdiction switcher in the
 * header lets us demo AU↔SG.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { useJurisdictionStore, type JurisdictionCode } from "../state/jurisdictionStore";

const API = import.meta.env.VITE_API_BASE || "";

interface Policy {
  policy_id: string;
  status: "managed" | "tracked" | "pending" | "lapsed";
  product: string;
  insurer_display: string;
  premium: { amount: number; currency: string; symbol: string };
  mocked_insurer: boolean;
}

interface Pack {
  code: string;
  display_name: string;
  currency_code: string;
  currency_symbol: string;
  rail_label: string;
  locale: string;
  regulator: string;
  disclosure_strings: Record<string, string>;
}

interface PoliciesResponse {
  policies: Policy[];
  jurisdiction: Pack;
  actual_jurisdiction: string;
  preview_mode: boolean;
}

export function ClientHome() {
  const preview = useJurisdictionStore((s) => s.preview);
  const setPreview = useJurisdictionStore((s) => s.setPreview);
  const [data, setData] = useState<PoliciesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const res = await fetch(`${API}/api/c/policies`, { credentials: "include" });
    if (!res.ok) {
      setError(`${res.status} ${res.statusText}`);
      return;
    }
    setData(await res.json());
  }
  useEffect(() => {
    load();
  }, [preview]);

  async function pick(code: JurisdictionCode | null) {
    setPreview(code);
    // Also persist server-side (dev-only endpoint).
    await fetch(`${API}/api/c/preferences`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jurisdiction: code }),
    });
  }

  return (
    <main className="surface-harbour brokaid-shell" data-testid="client-home-root">
      <header className="brokaid-shell__header">
        <span className="brokaid-shell__crumb">/c</span>
        {__AUTH_DEV_MODE__ && (
          <div className="brokaid-jurisdiction-switch" data-testid="jurisdiction-switch">
            <button
              type="button"
              className={preview === null || preview === "AU" ? "is-active" : ""}
              onClick={() => pick("AU")}
              data-testid="jurisdiction-au"
            >
              AU
            </button>
            <button
              type="button"
              className={preview === "SG" ? "is-active" : ""}
              onClick={() => pick("SG")}
              data-testid="jurisdiction-sg"
            >
              SG
            </button>
          </div>
        )}
      </header>

      {data?.preview_mode && (
        <div className="brokaid-preview-banner" data-testid="preview-banner">
          Preview: {data.jurisdiction.display_name} · actual is{" "}
          {data.actual_jurisdiction}
        </div>
      )}

      <h1 className="brokaid-shell__title">Your cover</h1>

      {error && <div className="brokaid-matters__error">{error}</div>}

      <section className="brokaid-cards" data-testid="client-policy-cards">
        {data?.policies.map((p) => (
          <article
            key={p.policy_id}
            className="brokaid-card"
            data-testid={`client-policy-card-${p.status}`}
          >
            <div className={`brokaid-chip brokaid-chip--${p.status}`}>
              {p.status}
            </div>
            <h2>{p.product}</h2>
            <div className="brokaid-card__row">
              <span>Insurer</span>
              <span>
                {p.insurer_display}{" "}
                {p.mocked_insurer && <MockedBadge label="carrier" />}
              </span>
            </div>
            <div className="brokaid-card__row">
              <span>Premium</span>
              <span className="brokaid-matters__mono">
                {p.premium.symbol}
                {new Intl.NumberFormat(data.jurisdiction.locale, {
                  maximumFractionDigits: 0,
                }).format(p.premium.amount)}{" "}
                {p.premium.currency}
              </span>
            </div>
            <div className="brokaid-card__row">
              <span>Payment rail</span>
              <span>{data.jurisdiction.rail_label}</span>
            </div>
          </article>
        ))}
      </section>

      {data && (
        <footer
          className="brokaid-disclosure"
          data-testid="client-disclosure"
        >
          <strong>Privacy notice.</strong>{" "}
          {data.jurisdiction.disclosure_strings.privacy_notice}
          <div className="brokaid-disclosure__reg">
            Regulated by {data.jurisdiction.regulator} · {data.jurisdiction.display_name}
          </div>
        </footer>
      )}

      <Link to="/" className="brokaid-shell__back" data-testid="link-back-home">
        Back to home
      </Link>
    </main>
  );
}

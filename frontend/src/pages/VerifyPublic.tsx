/**
 * VerifyPublic — /verify/:token
 *
 * PUBLIC, unauthenticated page. Fetches the public-safe certificate
 * subset from /api/verifier/:token. Harbour palette. Never renders
 * tenant_id, model_id, or anything internal. MOCKED · CARRIER pill on
 * the insurer field.
 */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";

const API = import.meta.env.VITE_API_BASE || "";

type PublicCert = {
  holder_display: string;
  insurer_display: string;
  product: string;
  cover_types: any[];
  effective_from: string;
  effective_to: string;
  issued_at: string;
  policy_id: string;
  mocked_insurer: true;
};

export function VerifyPublic() {
  const { token = "" } = useParams();
  const [state, setState] = useState<
    "loading" | "ok" | "not_found" | "revoked" | "rate_limited" | "expired" | "error"
  >("loading");
  const [cert, setCert] = useState<PublicCert | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/api/verifier/${token}`);
        if (r.status === 404) {
          setState("not_found");
          return;
        }
        if (r.status === 410) {
          const body = await r.json().catch(() => ({}));
          setState(body?.detail === "expired" ? "expired" : "revoked");
          return;
        }
        if (r.status === 429) {
          setState("rate_limited");
          return;
        }
        if (!r.ok) {
          setState("error");
          return;
        }
        setCert(await r.json());
        setState("ok");
      } catch {
        setState("error");
      }
    })();
  }, [token]);

  return (
    <main
      className="surface-harbour brokaid-verify"
      data-testid="verify-root"
    >
      <header className="brokaid-verify__head">
        <p className="brokaid-verify__mark">brok<em>AI</em>d</p>
        <h1
          className="brokaid-verify__title"
          data-testid="verify-title"
        >
          Certificate verification
        </h1>
      </header>

      {state === "loading" && (
        <p className="brokaid-verify__body" data-testid="verify-loading">
          Checking…
        </p>
      )}

      {state === "not_found" && (
        <p className="brokaid-verify__body" data-testid="verify-not-found">
          We couldn't find a certificate for that link.
        </p>
      )}

      {state === "revoked" && (
        <p className="brokaid-verify__body" data-testid="verify-revoked">
          This certificate link has been revoked by the broker.
        </p>
      )}

      {state === "expired" && (
        <p className="brokaid-verify__body" data-testid="verify-expired">
          This certificate link has expired.
        </p>
      )}

      {state === "rate_limited" && (
        <p className="brokaid-verify__body" data-testid="verify-rate-limited">
          You've checked too many links recently. Try again shortly.
        </p>
      )}

      {state === "error" && (
        <p className="brokaid-verify__body" data-testid="verify-error">
          Something went wrong. Please try again.
        </p>
      )}

      {state === "ok" && cert && (
        <article
          className="brokaid-verify__card"
          data-testid="verify-card"
        >
          <div className="brokaid-verify__row">
            <div>
              <p className="brokaid-verify__label">Insurer</p>
              <p
                className="brokaid-verify__value"
                data-testid="verify-insurer"
              >
                {cert.insurer_display}
              </p>
            </div>
            <MockedBadge label="carrier" variant="client" />
          </div>

          <div className="brokaid-verify__grid">
            <div>
              <p className="brokaid-verify__label">Holder</p>
              <p
                className="brokaid-verify__value"
                data-testid="verify-holder"
              >
                {cert.holder_display}
              </p>
            </div>
            <div>
              <p className="brokaid-verify__label">Product</p>
              <p className="brokaid-verify__value">{cert.product}</p>
            </div>
            <div>
              <p className="brokaid-verify__label">Effective</p>
              <p className="brokaid-verify__value">
                {new Date(cert.effective_from).toLocaleDateString()} —{" "}
                {new Date(cert.effective_to).toLocaleDateString()}
              </p>
            </div>
          </div>

          {cert.cover_types?.length > 0 && (
            <div className="brokaid-verify__cover-list">
              <p className="brokaid-verify__label">Cover</p>
              <ul>
                {cert.cover_types.map((c: any, i: number) => (
                  <li key={c?.type ?? i}>
                    {c.type} — limit any one {c.limit_any_one} {c.currency}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="brokaid-verify__footnote">
            Issued {new Date(cert.issued_at).toLocaleString()}
          </p>
        </article>
      )}
    </main>
  );
}

/**
 * ClientSign — /c/cover/:policy_id/sign/:envelope_id
 *
 * MOCKED e-sign. Backend emits SignatureRequested + SignatureCompleted,
 * both mocked=true. Renders MOCKED · E-SIGN pill.
 */
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";

const API = import.meta.env.VITE_API_BASE || "";

export function ClientSign() {
  const { policy_id = "", envelope_id = "" } = useParams();
  const navigate = useNavigate();
  const [state, setState] = useState<
    "idle" | "signing" | "signed" | "finalising" | "error"
  >("idle");
  const [err, setErr] = useState<string | null>(null);

  async function sign() {
    setState("signing");
    setErr(null);
    try {
      const r = await fetch(
        `${API}/api/c/matters/${policy_id}/sign/${envelope_id}`,
        { method: "POST", credentials: "include" }
      );
      if (!r.ok) {
        setErr(`${r.status}: ${await r.text()}`);
        setState("error");
        return;
      }
      setState("signed");
      setState("finalising");
      const f = await fetch(
        `${API}/api/c/matters/${policy_id}/finalise/${envelope_id}`,
        { method: "POST", credentials: "include" }
      );
      if (!f.ok) {
        setErr(`${f.status}: ${await f.text()}`);
        setState("error");
        return;
      }
      const fBody = await f.json();
      if (fBody.verifier_token) {
        window.sessionStorage.setItem(`vfy:${policy_id}`, fBody.verifier_token);
      }
      // Give a moment for state to settle, then redirect to certificate.
      setTimeout(
        () => navigate(`/c/cover/${policy_id}/certificate/${envelope_id}`),
        700
      );
    } catch (e: any) {
      setErr(String(e));
      setState("error");
    }
  }

  return (
    <section className="brokaid-tab" data-testid="client-sign-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Signing</p>
        <h1 className="brokaid-tab__title">Sign the endorsement</h1>
        <p className="brokaid-tab__lede">
          One tap to sign — no real signature engine is used for this demo.
        </p>
      </header>

      <article
        className="brokaid-quote-card brokaid-sign-card"
        data-testid="sign-card"
      >
        <div className="brokaid-quote-card__head">
          <div>
            <p className="brokaid-quote-card__label">Document</p>
            <h2 className="brokaid-quote-card__cover">Endorsement wording</h2>
          </div>
          <MockedBadge label="e-sign" variant="client" />
        </div>

        {state === "idle" && (
          <>
            <p className="brokaid-quote-card__summary">
              Tap sign to complete the endorsement. Your certificate will
              regenerate immediately after.
            </p>
            <div className="brokaid-quote-card__actions">
              <button
                type="button"
                className="brokaid-btn brokaid-btn--primary"
                onClick={sign}
                data-testid="sign-button"
              >
                Sign
              </button>
            </div>
          </>
        )}

        {(state === "signing" || state === "signed" || state === "finalising") && (
          <p
            className="brokaid-quote-card__summary"
            data-testid="sign-working"
          >
            {state === "signing"
              ? "Recording your signature…"
              : state === "signed"
              ? "Signed — executing endorsement…"
              : "Regenerating your certificate…"}
          </p>
        )}

        {state === "error" && (
          <p
            className="brokaid-quote-card__summary"
            data-testid="sign-error"
          >
            {err}
          </p>
        )}
      </article>
    </section>
  );
}

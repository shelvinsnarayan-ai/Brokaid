/**
 * Operator MFA enrolment — renders the otpauth QR as inline SVG,
 * then verifies the first code.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import DOMPurify from "dompurify";

const API = import.meta.env.VITE_API_BASE || "";

export function OperatorEnrol() {
  const nav = useNavigate();
  const [svg, setSvg] = useState<string | null>(null);
  const [uri, setUri] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const res = await fetch(`${API}/api/auth/operator/mfa/enrol`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        setError((await res.json().catch(() => ({}))).detail || `${res.status}`);
        return;
      }
      const b = await res.json();
      setSvg(b.qr_svg);
      setUri(b.otpauth_uri);
    })();
  }, []);

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const res = await fetch(`${API}/api/auth/operator/mfa/verify`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (!res.ok) {
      setError((await res.json().catch(() => ({}))).detail || `${res.status}`);
      return;
    }
    nav("/o");
  }

  return (
    <main className="surface-ledger brokaid-shell" data-testid="operator-enrol-root">
      <header className="brokaid-shell__header">
        <span className="brokaid-shell__crumb">/o/enrol</span>
      </header>
      <h1 className="brokaid-shell__title">Enrol authenticator</h1>
      <p className="brokaid-shell__note">
        Scan the QR with your authenticator app, then enter the current code.
      </p>

      {svg ? (
        <div
          className="brokaid-enrol__qr"
          /* The QR SVG is server-generated from an otpauth URI that
           * embeds the user's email — user-controllable content. We
           * sanitise defence-in-depth with the DOMPurify SVG profile
           * before injection. */
          dangerouslySetInnerHTML={{
            __html: DOMPurify.sanitize(svg, {
              USE_PROFILES: { svg: true, svgFilters: true },
            }),
          }}
          data-testid="op-enrol-qr"
        />
      ) : (
        <div>Loading&hellip;</div>
      )}

      {uri && (
        <details className="brokaid-enrol__uri">
          <summary>Manual key</summary>
          <code>{uri}</code>
        </details>
      )}

      <form onSubmit={verify} className="brokaid-form">
        <label className="brokaid-form__label">
          Code
          <input
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            data-testid="op-enrol-code"
          />
        </label>
        <button type="submit" data-testid="op-enrol-verify">
          Verify
        </button>
      </form>

      {error && (
        <div className="brokaid-form__err" data-testid="op-enrol-error">
          {error}
        </div>
      )}
    </main>
  );
}

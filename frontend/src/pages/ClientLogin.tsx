/**
 * Client login — real API. Shows the dev-mode token inline when the
 * backend returns it (i.e. AUTH_DEV_MODE=true and RESEND unavailable).
 */
import { useState } from "react";
import { MockedBadge } from "@brokaid/mocks";
import { BrandWordmark } from "../components/BrandWordmark";

const API = import.meta.env.VITE_API_BASE || "";

export function ClientLogin() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "sent" | "verified" | "error">("idle");
  const [devToken, setDevToken] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("idle");
    setErrorMsg(null);
    const res = await fetch(`${API}/api/auth/client/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setErrorMsg(data.detail || `${res.status} ${res.statusText}`);
      setStatus("error");
      return;
    }
    const body = await res.json();
    if (body.dev_mode && body.dev_token) setDevToken(body.dev_token);
    setStatus("sent");
  }

  async function verify() {
    if (!devToken) return;
    const res = await fetch(
      `${API}/api/auth/client/verify?token=${encodeURIComponent(devToken)}`,
      { credentials: "include" }
    );
    if (res.ok) setStatus("verified");
    else {
      const data = await res.json().catch(() => ({}));
      setErrorMsg(data.detail || `${res.status} ${res.statusText}`);
      setStatus("error");
    }
  }

  return (
    <main className="surface-harbour brokaid-shell" data-testid="client-login-root">
      <header className="brokaid-shell__header">
        <BrandWordmark variant="harbour-bold" height={32} />
        <span className="brokaid-shell__crumb">/c/login</span>
      </header>
      <h1 className="brokaid-shell__title">Sign in</h1>
      <p className="brokaid-shell__note">
        We&rsquo;ll email a one-time link. Nothing to memorise.
      </p>

      <form onSubmit={submit} className="brokaid-form">
        <label className="brokaid-form__label">
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            data-testid="client-login-email"
          />
        </label>
        <button type="submit" data-testid="client-login-submit">
          Send link
        </button>
      </form>

      {status === "sent" && (
        <div className="brokaid-form__msg" data-testid="client-login-sent">
          Link sent. Check your inbox.
        </div>
      )}
      {status === "verified" && (
        <div className="brokaid-form__msg" data-testid="client-login-verified">
          Signed in.
        </div>
      )}
      {errorMsg && (
        <div className="brokaid-form__err" data-testid="client-login-error">
          {errorMsg}
        </div>
      )}

      {devToken && (
        <div className="brokaid-devbox" data-testid="client-login-dev-token">
          <MockedBadge label="e-sign" />
          <div>
            <strong>Dev token</strong> (no email provider — dev-mode fallback):
          </div>
          <code>{devToken}</code>
          <button
            type="button"
            onClick={verify}
            data-testid="client-login-dev-verify"
          >
            Verify link
          </button>
        </div>
      )}
    </main>
  );
}

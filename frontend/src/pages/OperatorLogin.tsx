/**
 * Operator login — password step, then TOTP verify.
 */
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { BrandWordmark } from "../components/BrandWordmark";

const API = import.meta.env.VITE_API_BASE || "";

export function OperatorLogin() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needMfa, setNeedMfa] = useState(false);
  const [needEnrol, setNeedEnrol] = useState(false);
  const [code, setCode] = useState("");

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const res = await fetch(`${API}/api/auth/operator/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      setError(b.detail || `${res.status}`);
      return;
    }
    const b = await res.json();
    setNeedMfa(b.mfa_required);
    setNeedEnrol(b.enrolment_required);
  }

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
      const b = await res.json().catch(() => ({}));
      setError(b.detail || `${res.status}`);
      return;
    }
    nav("/o");
  }

  return (
    <main className="surface-ledger brokaid-shell" data-testid="operator-login-root">
      <header className="brokaid-shell__header">
        <BrandWordmark variant="ledger-reverse" height={32} />
        <span className="brokaid-shell__crumb">/o/login</span>
      </header>
      <h1 className="brokaid-shell__title">Operator sign-in</h1>

      {!needMfa && (
        <form onSubmit={submitLogin} className="brokaid-form">
          <label className="brokaid-form__label">
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="op-login-email"
            />
          </label>
          <label className="brokaid-form__label">
            Password
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="op-login-password"
            />
          </label>
          <button type="submit" data-testid="op-login-submit">
            Continue
          </button>
        </form>
      )}

      {needMfa && needEnrol && (
        <div className="brokaid-form__msg">
          Enrolment required.{" "}
          <Link to="/o/enrol" data-testid="op-login-enrol-link">
            Enrol authenticator app →
          </Link>
        </div>
      )}

      {needMfa && !needEnrol && (
        <form onSubmit={verify} className="brokaid-form">
          <label className="brokaid-form__label">
            6-digit code
            <input
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              data-testid="op-login-totp"
            />
          </label>
          <button type="submit" data-testid="op-login-verify">
            Verify
          </button>
        </form>
      )}

      {error && (
        <div className="brokaid-form__err" data-testid="op-login-error">
          {error}
        </div>
      )}
    </main>
  );
}

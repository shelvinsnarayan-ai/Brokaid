import { useState } from "react";
import { Link } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { AuthCard, AuthError } from "../../components/auth/AuthCard";
import { MfaEnrol } from "../../components/auth/MfaEnrol";

type Step = "password" | "totp" | "enrol" | "forgot" | "forgot-sent";

export default function SignIn() {
  const [step, setStep] = useState<Step>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setErr(null);
    setBusy(true);
    try { await fn(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  const go = (redirect: string) => { window.location.assign(redirect); };

  const submitPassword = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    const r = await authCall<{ next: Step }>("auth/login", { email, password });
    setPassword("");
    setStep(r.next);
  }); };
  const submitCode = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    const r = await authCall<{ redirect: string }>("auth/login/verify", useRecovery ? { recovery_code: code } : { code });
    go(r.redirect);
  }); };
  const submitForgot = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    await authCall("auth/password/forgot", { email });
    setStep("forgot-sent");
  }); };

  if (step === "enrol") {
    return (
      <AuthCard title="Set up your authenticator" testid="signin-enrol-root"
        lead="Your brokerage needs you to turn on an authenticator app before you continue.">
        <MfaEnrol onDone={go} />
      </AuthCard>
    );
  }
  if (step === "totp") {
    return (
      <AuthCard title="Enter your code" testid="signin-totp-root"
        lead={useRecovery ? "Type one of your saved recovery codes." : "Open your authenticator app and type the 6-digit code."}>
        <form onSubmit={submitCode} className="auth-form">
          <label className="auth-label">
            {useRecovery ? "Recovery code" : "6-digit code"}
            <input autoFocus inputMode={useRecovery ? "text" : "numeric"} autoComplete="one-time-code" required value={code}
              onChange={(e) => setCode(e.target.value)} data-testid="signin-code" />
          </label>
          <button type="submit" className="auth-btn" disabled={busy} data-testid="signin-code-submit">{busy ? "Checking…" : "Sign in"}</button>
        </form>
        <AuthError msg={err} testid="signin-code-error" />
        <button type="button" className="auth-link" onClick={() => { setUseRecovery(!useRecovery); setCode(""); setErr(null); }}
          data-testid="signin-toggle-recovery">
          {useRecovery ? "Use my authenticator app instead" : "Lost your phone? Use a recovery code"}
        </button>
      </AuthCard>
    );
  }
  if (step === "forgot" || step === "forgot-sent") {
    return (
      <AuthCard title="Reset your password" testid="signin-forgot-root"
        lead={step === "forgot-sent" ? "If that email has an account, we've sent a reset link. It works once, for 1 hour."
          : "Enter your email and we'll send you a link to choose a new password."}>
        {step === "forgot" && (
          <form onSubmit={submitForgot} className="auth-form">
            <label className="auth-label">Email
              <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} data-testid="forgot-email" />
            </label>
            <button type="submit" className="auth-btn" disabled={busy} data-testid="forgot-submit">Send reset link</button>
          </form>
        )}
        <AuthError msg={err} testid="forgot-error" />
        <button type="button" className="auth-link" onClick={() => setStep("password")} data-testid="forgot-back">Back to sign in</button>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="Sign in" testid="signin-root" lead="For brokerage staff and clients.">
      <form onSubmit={submitPassword} className="auth-form">
        <label className="auth-label">Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} data-testid="signin-email" />
        </label>
        <label className="auth-label">Password
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} data-testid="signin-password" />
        </label>
        <button type="submit" className="auth-btn" disabled={busy} data-testid="signin-submit">{busy ? "Checking…" : "Continue"}</button>
      </form>
      <AuthError msg={err} testid="signin-error" />
      <button type="button" className="auth-link" onClick={() => { setErr(null); setStep("forgot"); }} data-testid="signin-forgot">Forgot your password?</button>
      <p className="auth-fine">New here? You need an invite from your brokerage.</p>
      {import.meta.env.BROKAID_AUTH_DEV_MODE ? (
        <Link to="/demo" className="auth-link" data-testid="signin-demo-link">Preview only: demo personas</Link>
      ) : null}
    </AuthCard>
  );
}

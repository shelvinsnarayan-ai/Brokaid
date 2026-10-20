import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { AuthCard, AuthError } from "../../components/auth/AuthCard";
import { MfaEnrol } from "../../components/auth/MfaEnrol";
import { DemoSignIn } from "../../components/auth/DemoSignIn";
import { DemoHostBanner } from "../../components/auth/DemoHostBanner";
import { googleRedirect, takeGoogleSessionId, TenantChooser, TenantOpt } from "../../components/auth/Tenants";

type Step = "password" | "totp" | "enrol" | "forgot" | "forgot-sent" | "choose";

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
  const [done, setDone] = useState<{ redirect: string; tenants: TenantOpt[] } | null>(null);
  const go = (redirect: string, tenants: TenantOpt[] = []) => {
    if (tenants.length > 1) { setDone({ redirect, tenants }); setStep("choose"); } else window.location.assign(redirect);
  };
  useEffect(() => {
    const sid = takeGoogleSessionId();
    if (sid) run(async () => setStep((await authCall<{ next: Step }>("auth/google", { session_id: sid })).next));
  }, []);

  const submitPassword = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    const r = await authCall<{ next: Step }>("auth/login", { email, password });
    setPassword("");
    setStep(r.next);
  }); };
  const submitCode = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    const r = await authCall<{ redirect: string; tenants: TenantOpt[] }>("auth/login/verify", useRecovery ? { recovery_code: code } : { code });
    go(r.redirect, r.tenants);
  }); };
  const submitForgot = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    await authCall("auth/password/forgot", { email });
    setStep("forgot-sent");
  }); };

  if (step === "choose" && done) {
    return <AuthCard title="Choose a brokerage" testid="signin-choose-root"><TenantChooser {...done} /></AuthCard>;
  }
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
      <DemoHostBanner />
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
      <div className="auth-or"><span>or</span></div>
      <button type="button" className="auth-btn auth-btn--ghost auth-btn--google" onClick={() => googleRedirect("/login")}
        data-testid="signin-google">
        <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
        Continue with Google
      </button>
      <p className="auth-fine">Google works only for an email your brokerage invited. You'll still enter your authenticator code.</p>
      <button type="button" className="auth-link" onClick={() => { setErr(null); setStep("forgot"); }} data-testid="signin-forgot">Forgot password?</button>
      <p className="auth-fine">New here? You need an invite from your brokerage.</p>
      {import.meta.env.BROKAID_DEMO_SIGNIN ? <DemoSignIn /> : null}
      {import.meta.env.BROKAID_AUTH_DEV_MODE ? (
        <Link to="/demo" className="auth-link" data-testid="signin-demo-link">Preview only: demo personas</Link>
      ) : null}
    </AuthCard>
  );
}

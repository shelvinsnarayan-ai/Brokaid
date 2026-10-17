import { useEffect, useState } from "react";
import { authCall } from "../../lib/authApi";
import { AuthCard, AuthError } from "../../components/auth/AuthCard";
import { googleRedirect, takeGoogleSessionId, TenantSwitcher } from "../../components/auth/Tenants";

function GoogleLink({ onMsg, onErr }: { onMsg: (m: string) => void; onErr: (m: string) => void }) {
  const [linked, setLinked] = useState<string | null>(null);
  useEffect(() => {
    authCall<{ google_email: string | null }>("auth/account", undefined, "GET").then((r) => setLinked(r.google_email)).catch(() => undefined);
    const sid = takeGoogleSessionId();
    if (sid) authCall<{ google_email: string }>("auth/google/link", { session_id: sid })
      .then((r) => { setLinked(r.google_email); onMsg("Google is linked. You can now use Continue with Google."); })
      .catch((e) => onErr(e.message));
  }, []);
  return (
    <section className="auth-section" data-testid="security-google">
      <h2>Google sign-in</h2>
      <p className="auth-copy" data-testid="security-google-status">
        {linked ? `Linked to ${linked}. You'll still enter your authenticator code.` : "Not linked. Google must use the same email as your sign-in."}
      </p>
      {!linked && (
        <button type="button" className="auth-btn auth-btn--ghost" onClick={() => googleRedirect("/account/security")}
          data-testid="security-google-link">Link Google</button>
      )}
    </section>
  );
}

export default function AccountSecurity() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function act(fn: () => Promise<string>) {
    setErr(null); setMsg(null);
    try { setMsg(await fn()); } catch (e: any) { setErr(e.message); }
  }
  const change = (e: React.FormEvent) => { e.preventDefault(); act(async () => {
    const r = await authCall<{ other_sessions_signed_out: number }>("auth/password/change", { current_password: current, new_password: next });
    setCurrent(""); setNext("");
    return `Password changed. ${r.other_sessions_signed_out} other device(s) signed out.`;
  }); };
  const others = () => act(async () => {
    const r = await authCall<{ signed_out: number }>("auth/sessions/revoke-others");
    return `${r.signed_out} other device(s) signed out.`;
  });

  return (
    <AuthCard title="Sign-in and security" testid="security-root">
      <form onSubmit={change} className="auth-form">
        <label className="auth-label">Current password
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} data-testid="security-current-password" />
        </label>
        <label className="auth-label">New password (12+ characters)
          <input type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} data-testid="security-new-password" />
        </label>
        <button type="submit" className="auth-btn" data-testid="security-change-submit">Change password</button>
      </form>
      <button type="button" className="auth-btn auth-btn--ghost" onClick={others} data-testid="security-signout-others">Sign out other devices</button>
      <GoogleLink onMsg={setMsg} onErr={setErr} />
      <section className="auth-section"><h2>Brokerage</h2><TenantSwitcher testid="security-tenant-switcher" /></section>
      {msg ? <p className="auth-ok" data-testid="security-msg">{msg}</p> : null}
      <AuthError msg={err} testid="security-error" />
      <button type="button" className="auth-link" onClick={() => window.history.back()} data-testid="security-back">Back</button>
    </AuthCard>
  );
}

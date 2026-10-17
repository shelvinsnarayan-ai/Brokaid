import { useState } from "react";
import { authCall } from "../../lib/authApi";
import { AuthCard, AuthError } from "../../components/auth/AuthCard";

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
      {msg ? <p className="auth-ok" data-testid="security-msg">{msg}</p> : null}
      <AuthError msg={err} testid="security-error" />
      <button type="button" className="auth-link" onClick={() => window.history.back()} data-testid="security-back">Back</button>
    </AuthCard>
  );
}

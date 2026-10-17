import { useState } from "react";
import { AuthError } from "./AuthCard";

export function NewPassword({ email, cta, testid, submit }: {
  email: string; cta: string; testid: string; submit: (password: string) => Promise<void>;
}) {
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (pw.length < 12) return setErr("Use at least 12 characters.");
    if (pw !== again) return setErr("The two passwords don't match.");
    setBusy(true);
    try { await submit(pw); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={onSubmit} className="auth-form">
      <input type="email" autoComplete="username" value={email} readOnly hidden />
      <label className="auth-label">New password
        <input type="password" autoComplete="new-password" required value={pw} onChange={(e) => setPw(e.target.value)} data-testid={`${testid}-password`} />
      </label>
      <p className="auth-hint" data-testid={`${testid}-password-hint`}>
        At least 12 characters. A short phrase of unrelated words works well. {pw.length > 0 ? `(${pw.length}/12)` : ""}
      </p>
      <label className="auth-label">Type it again
        <input type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} data-testid={`${testid}-password-again`} />
      </label>
      <button type="submit" className="auth-btn" disabled={busy} data-testid={`${testid}-password-submit`}>{busy ? "Saving…" : cta}</button>
      <AuthError msg={err} testid={`${testid}-password-error`} />
    </form>
  );
}

import { useEffect, useState } from "react";
import { authCall } from "../../lib/authApi";
import { AuthError } from "./AuthCard";
import { RecoveryCodes } from "./RecoveryCodes";
import type { TenantOpt } from "./Tenants";

interface Setup { secret: string; qr_svg: string }

export function MfaEnrol({ onDone }: { onDone: (redirect: string, tenants?: TenantOpt[]) => void }) {
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ redirect: string; codes: string[]; tenants: TenantOpt[] } | null>(null);

  useEffect(() => {
    authCall<Setup>("auth/mfa/setup").then(setSetup).catch((e) => setErr(e.message));
  }, []);

  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await authCall<{ redirect: string; recovery_codes: string[]; tenants: TenantOpt[] }>("auth/mfa/confirm", { code });
      setDone({ redirect: r.redirect, codes: r.recovery_codes, tenants: r.tenants });
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) return <RecoveryCodes codes={done.codes} onContinue={() => onDone(done.redirect, done.tenants)} />;
  return (
    <div className="auth-steps" data-testid="mfa-enrol">
      <p className="auth-step">Step 2 of 3 · Turn on your authenticator</p>
      <p className="auth-copy">Scan this with an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password).</p>
      {setup ? (
        <>
          <div className="auth-qr" data-testid="mfa-qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
          <details className="auth-manual">
            <summary data-testid="mfa-manual-toggle">Can't scan? Type this key instead</summary>
            <code data-testid="mfa-manual-key">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
          </details>
          <form onSubmit={confirm} className="auth-form">
            <label className="auth-label">
              6-digit code from the app
              <input inputMode="numeric" autoComplete="one-time-code" maxLength={7} required value={code}
                onChange={(e) => setCode(e.target.value)} data-testid="mfa-enrol-code" />
            </label>
            <button type="submit" className="auth-btn" disabled={busy} data-testid="mfa-enrol-submit">
              {busy ? "Checking…" : "Turn on"}
            </button>
          </form>
        </>
      ) : !err ? <p className="auth-copy">Preparing your key…</p> : null}
      <AuthError msg={err} testid="mfa-enrol-error" />
    </div>
  );
}

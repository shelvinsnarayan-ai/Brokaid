import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { AuthCard, AuthError } from "../../components/auth/AuthCard";
import { MfaEnrol } from "../../components/auth/MfaEnrol";
import { NewPassword } from "../../components/auth/NewPassword";
import { TenantChooser, TenantOpt } from "../../components/auth/Tenants";

interface Info { email: string; name: string; role: string; brokerage: string; existing_account: boolean; collection_notice?: string; privacy_policy?: string }

function ExistingAccount({ token, onNext }: { token: string; onNext: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null);
    try { await authCall(`auth/invite/${token}/accept`, { password: pw }); onNext(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <form onSubmit={submit} className="auth-form">
      <p className="auth-copy">You already have a brokAId sign-in. Confirm it to join this brokerage.</p>
      <label className="auth-label">Your current password
        <input type="password" autoComplete="current-password" required value={pw} onChange={(e) => setPw(e.target.value)} data-testid="invite-existing-password" />
      </label>
      <button type="submit" className="auth-btn" data-testid="invite-existing-submit">Continue</button>
      <AuthError msg={err} testid="invite-existing-error" />
    </form>
  );
}

function CodeStep({ onDone }: { onDone: (redirect: string, tenants: TenantOpt[]) => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null);
    try {
      const r = await authCall<{ redirect: string; tenants: TenantOpt[] }>("auth/login/verify", { code });
      onDone(r.redirect, r.tenants);
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <form onSubmit={submit} className="auth-form">
      <label className="auth-label">6-digit code from your authenticator
        <input inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} data-testid="invite-existing-code" />
      </label>
      <button type="submit" className="auth-btn" data-testid="invite-existing-code-submit">Join</button>
      <AuthError msg={err} testid="invite-existing-code-error" />
    </form>
  );
}

export default function AcceptInvite() {
  const { token = "" } = useParams();
  const [info, setInfo] = useState<Info | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [step, setStep] = useState<"password" | "enrol" | "code" | "choose">("password");
  const [done, setDone] = useState<{ redirect: string; tenants: TenantOpt[] } | null>(null);
  const finish = (redirect: string, tenants: TenantOpt[] = []) => {
    if (tenants.length > 1) { setDone({ redirect, tenants }); setStep("choose"); } else window.location.assign(redirect);
  };

  useEffect(() => {
    authCall<Info>(`auth/invite/${token}`, undefined, "GET").then(setInfo).catch((e) => setErr(e.message));
  }, [token]);

  if (step === "choose" && done) return <AuthCard title="Choose a brokerage" testid="invite-choose-root"><TenantChooser {...done} /></AuthCard>;
  if (step === "enrol") return <AuthCard title="Set up your authenticator" testid="invite-enrol-root"><MfaEnrol onDone={finish} /></AuthCard>;
  if (step === "code") return <AuthCard title="Enter your code" testid="invite-code-root"><CodeStep onDone={finish} /></AuthCard>;
  if (!info) {
    return (
      <AuthCard title="Your invite" testid="invite-root">
        {err ? <AuthError msg={err} testid="invite-error" /> : <p className="auth-copy">Checking your invite…</p>}
        {err ? <a className="auth-link" href="/login" data-testid="invite-to-login">Go to sign in</a> : null}
      </AuthCard>
    );
  }
  return (
    <AuthCard title={`Welcome, ${info.name.split(" ")[0]}`} testid="invite-root"
      lead={<>You've been invited to <strong>{info.brokerage}</strong> as <strong data-testid="invite-role">{info.role}</strong>.</>}>
      <p className="auth-copy" data-testid="invite-email">Signing in as {info.email}</p>
      {info.collection_notice ? <details className="auth-copy" data-testid="invite-collection-notice" open><summary>How we handle your information</summary><p>{info.collection_notice}</p><p>{info.privacy_policy}</p><p>By continuing you confirm you've read this notice. We'll also give you our Financial Services Guide.</p></details> : null}
      {info.existing_account ? <ExistingAccount token={token} onNext={() => setStep("code")} /> : (
        <>
          <p className="auth-step">Step 1 of 3 · Choose a password</p>
          <NewPassword email={info.email} cta="Set password" testid="invite"
            submit={async (password) => { await authCall(`auth/invite/${token}/accept`, { password }); setStep("enrol"); }} />
        </>
      )}
    </AuthCard>
  );
}

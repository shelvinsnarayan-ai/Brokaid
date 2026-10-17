import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { AuthCard, AuthError } from "../../components/auth/AuthCard";
import { MfaEnrol } from "../../components/auth/MfaEnrol";
import { NewPassword } from "../../components/auth/NewPassword";

interface Info { email: string; name: string; role: string; brokerage: string }

export default function AcceptInvite() {
  const { token = "" } = useParams();
  const [info, setInfo] = useState<Info | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [enrol, setEnrol] = useState(false);

  useEffect(() => {
    authCall<Info>(`auth/invite/${token}`, undefined, "GET").then(setInfo).catch((e) => setErr(e.message));
  }, [token]);

  if (enrol) {
    return <AuthCard title="Set up your authenticator" testid="invite-enrol-root"><MfaEnrol onDone={(r) => window.location.assign(r)} /></AuthCard>;
  }
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
      <p className="auth-step">Step 1 of 3 · Choose a password</p>
      <p className="auth-copy" data-testid="invite-email">Signing in as {info.email}</p>
      <NewPassword email={info.email} cta="Set password" testid="invite"
        submit={async (password) => { await authCall(`auth/invite/${token}/accept`, { password }); setEnrol(true); }} />
    </AuthCard>
  );
}

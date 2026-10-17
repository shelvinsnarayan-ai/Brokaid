import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { AuthCard, AuthError } from "../../components/auth/AuthCard";
import { NewPassword } from "../../components/auth/NewPassword";

export default function ResetPassword() {
  const { token = "" } = useParams();
  const [state, setState] = useState<"checking" | "ready" | "done" | "bad">("checking");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    authCall(`auth/password/reset/${token}`, undefined, "GET").then(() => setState("ready"))
      .catch((e) => { setErr(e.message); setState("bad"); });
  }, [token]);

  return (
    <AuthCard title="Choose a new password" testid="reset-root"
      lead={state === "done" ? "Your password is changed and other devices are signed out." : undefined}>
      {state === "checking" && <p className="auth-copy">Checking your link…</p>}
      {state === "ready" && (
        <NewPassword email="" cta="Save new password" testid="reset"
          submit={async (password) => { await authCall("auth/password/reset", { token, password }); setState("done"); }} />
      )}
      {state === "bad" && <AuthError msg={err} testid="reset-error" />}
      {state !== "checking" && state !== "ready" && (
        <a className="auth-btn auth-btn--link" href="/login" data-testid="reset-to-login">Go to sign in</a>
      )}
    </AuthCard>
  );
}

/*! brokaid:dev-only */
/**
 * DEV-ONLY role switcher.
 *
 * Calls `/api/auth/dev/impersonate` (mounted only when AUTH_DEV_MODE=true)
 * to pick a seeded user and set a session cookie. Not compiled into
 * production builds — the top-level marker keeps the file's chunk out of
 * the deny-list scan.
 */
import { useNavigate } from "react-router-dom";

const API = import.meta.env.VITE_API_BASE || "";

interface Persona {
  label: string;
  email: string;
  next: string;
  testId: string;
}

const PERSONAS: Persona[] = [
  { label: "Principal (Ada)",        email: "principal@burmac.example",   next: "/o", testId: "dev-role-principal" },
  { label: "Deputy (Ben)",           email: "deputy@burmac.example",      next: "/o", testId: "dev-role-deputy" },
  { label: "Account Exec (Cass)",    email: "ae@burmac.example",          next: "/o", testId: "dev-role-ae" },
  { label: "Accounts A (Dee)",       email: "accounts-a@burmac.example",  next: "/o", testId: "dev-role-accounts-a" },
  { label: "Accounts B (Ev)",        email: "accounts-b@burmac.example",  next: "/o", testId: "dev-role-accounts-b" },
  { label: "Compliance (Fin)",       email: "compliance@burmac.example",  next: "/o", testId: "dev-role-compliance" },
  { label: "Client owner (Kai)",     email: "owner@fictionalclient.example",   next: "/c", testId: "dev-role-client-owner" },
  { label: "Client finance (Sam)",   email: "finance@fictionalclient.example", next: "/c", testId: "dev-role-client-finance" },
];

export function DevLogin() {
  const navigate = useNavigate();

  async function pick(persona: Persona) {
    const res = await fetch(`${API}/api/auth/dev/impersonate`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: persona.email }),
    });
    if (!res.ok) {
      alert(`impersonate failed: ${res.status}`);
      return;
    }
    navigate(persona.next);
  }

  return (
    <main className="brokaid-dev-login" data-testid="dev-login-root">
      <div className="brokaid-dev-login__card">
        <h1 className="brokaid-dev-login__title">Dev impersonation</h1>
        <div className="brokaid-dev-login__hint">
          Signs you in as a seeded fictional user. Dev password:{" "}
          <code>dev-password-not-for-production</code>. Not available in
          production builds.
        </div>
        <div className="brokaid-dev-login__roles">
          {PERSONAS.map((p) => (
            <button
              key={p.email}
              type="button"
              className="brokaid-dev-login__role"
              onClick={() => pick(p)}
              data-testid={p.testId}
            >
              Continue as {p.label} → {p.next}
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}

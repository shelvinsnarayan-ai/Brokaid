/*! brokaid:dev-only */
import { useNavigate } from "react-router-dom";
import { useAuthStore, type Role } from "../state/authStore";

/**
 * DEV-ONLY. Not compiled into production builds.
 *
 * Real auth lands in phase 4. This route only exists to switch a role
 * in the Zustand store so downstream phases can develop against role
 * gates. The file is tagged with the `brokaid:dev-only` marker so the
 * CI deny-list scanner skips its chunk.
 */
export function DevLogin() {
  const navigate = useNavigate();
  const setRole = useAuthStore((s) => s.setRole);

  function pick(role: Role, next: string) {
    setRole(role);
    navigate(next);
  }

  return (
    <main className="brokaid-dev-login" data-testid="dev-login-root">
      <div className="brokaid-dev-login__card">
        <h1 className="brokaid-dev-login__title">Dev role switcher</h1>
        <div className="brokaid-dev-login__hint">
          Sets a role in the local store. No credentials, no server call.
        </div>
        <div className="brokaid-dev-login__roles">
          <button
            type="button"
            className="brokaid-dev-login__role"
            onClick={() => pick("client", "/c")}
            data-testid="dev-role-client"
          >
            Continue as CLIENT → /c
          </button>
          <button
            type="button"
            className="brokaid-dev-login__role"
            onClick={() => pick("broker", "/o")}
            data-testid="dev-role-broker"
          >
            Continue as BROKER → /o
          </button>
          <button
            type="button"
            className="brokaid-dev-login__role"
            onClick={() => pick("admin", "/o")}
            data-testid="dev-role-admin"
          >
            Continue as ADMIN → /o
          </button>
        </div>
      </div>
    </main>
  );
}

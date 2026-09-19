/*  frontend/src/pages/OperatorMatters.tsx
 *
 *  Phase 1 demonstrator — pulls the `proj_matters` projection via
 *  `/api/o/matters`, renders a keyboard-navigable table on the
 *  Ledger surface.
 *
 *  Phase 1 auth is header-based (`X-Tenant-Slug`); real auth lands
 *  phase 4.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

interface Matter {
  matter_id: string;
  client_id: string;
  status: string;
  subject: string;
  updated_at: string;
  updated_by_event: string;
}

const API = import.meta.env.VITE_API_BASE || "";

export function OperatorMatters() {
  const [matters, setMatters] = useState<Matter[]>([]);
  const [tenantSlug, setTenantSlug] = useState<string>(
    () => localStorage.getItem("brokaid.tenant") ?? "acme"
  );
  const [tenantName, setTenantName] = useState<string>("");
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(`${API}/api/o/matters`, {
        headers: { "X-Tenant-Slug": tenantSlug },
      });
      if (!res.ok) {
        setMatters([]);
        setTenantName("");
        setError(`${res.status} ${res.statusText}`);
        return;
      }
      const data = await res.json();
      setMatters(data.matters);
      setTenantName(data.tenant.display_name);
    } catch (e) {
      setError(String(e));
    }
  }, [tenantSlug]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    localStorage.setItem("brokaid.tenant", tenantSlug);
  }, [tenantSlug]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "j") setSelected((s) => Math.min(s + 1, matters.length - 1));
      else if (e.key === "k") setSelected((s) => Math.max(s - 1, 0));
      else if (e.key === "Enter" && matters[selected]) {
        // stub: real detail route lands later
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [matters, selected]);

  return (
    <main
      className="surface-ledger brokaid-matters"
      data-testid="matters-root"
    >
      <header className="brokaid-matters__header">
        <div className="brokaid-shell__crumb">/o/matters</div>
        <input
          className="brokaid-matters__tenant"
          value={tenantSlug}
          onChange={(e) => setTenantSlug(e.target.value)}
          spellCheck={false}
          data-testid="matters-tenant-input"
          aria-label="Tenant slug"
        />
      </header>

      <h1 className="brokaid-shell__title">Matters</h1>
      <p className="brokaid-matters__note">
        Live projection from the append-only ledger. Navigate with{" "}
        <kbd>j</kbd>/<kbd>k</kbd>, open with <kbd>Enter</kbd>.
        {tenantName ? ` Tenant: ${tenantName}.` : ""}
      </p>

      {error && (
        <div className="brokaid-matters__error" data-testid="matters-error">
          {error}
        </div>
      )}

      <table className="brokaid-matters__table" data-testid="matters-table">
        <thead>
          <tr>
            <th>Subject</th>
            <th>Status</th>
            <th>Updated</th>
            <th>Matter ID</th>
          </tr>
        </thead>
        <tbody>
          {matters.map((m, i) => (
            <tr
              key={m.matter_id}
              className={i === selected ? "is-selected" : ""}
              onClick={() => setSelected(i)}
              data-testid={`matter-row-${i}`}
            >
              <td>{m.subject}</td>
              <td>
                <span className="brokaid-matters__status">{m.status}</span>
              </td>
              <td className="brokaid-matters__mono">
                {new Date(m.updated_at).toISOString().replace("T", " ").slice(0, 19)}
              </td>
              <td className="brokaid-matters__mono">
                {m.matter_id.slice(0, 8)}…
              </td>
            </tr>
          ))}
          {matters.length === 0 && !error && (
            <tr>
              <td colSpan={4} className="brokaid-matters__empty">
                No matters yet for tenant <code>{tenantSlug}</code>.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <Link
        to="/o"
        className="brokaid-shell__back"
        data-testid="link-back-operator"
      >
        Back
      </Link>
    </main>
  );
}

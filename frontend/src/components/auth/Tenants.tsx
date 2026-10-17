import { useEffect, useState } from "react";
import { authCall } from "../../lib/authApi";

export interface TenantOpt { tenant_id: string; name: string; current: boolean }

export const googleRedirect = (path: string) => {
  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
  window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(window.location.origin + path)}`;
};

export function takeGoogleSessionId(): string | null {
  const m = window.location.hash.match(/session_id=([^&]+)/);
  if (!m) return null;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return decodeURIComponent(m[1]);
}

export async function switchTenant(tenantId: string): Promise<string> {
  const r = await authCall<{ redirect: string }>("auth/switch-tenant", { tenant_id: tenantId });
  return r.redirect;
}

export function TenantChooser({ tenants, redirect }: { tenants: TenantOpt[]; redirect: string }) {
  const [err, setErr] = useState<string | null>(null);
  const pick = async (t: TenantOpt) => {
    try { window.location.assign(t.current ? redirect : await switchTenant(t.tenant_id)); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="auth-steps" data-testid="tenant-chooser">
      <p className="auth-copy">You have access to more than one brokerage. Which one do you want to open?</p>
      {tenants.map((t) => (
        <button key={t.tenant_id} type="button" className="auth-btn auth-btn--ghost" onClick={() => pick(t)}
          data-testid={`tenant-choose-${t.tenant_id}`}>{t.name}</button>
      ))}
      {err ? <p className="auth-err" role="alert" data-testid="tenant-chooser-error">{err}</p> : null}
    </div>
  );
}

export function TenantSwitcher({ testid = "tenant-switcher" }: { testid?: string }) {
  const [tenants, setTenants] = useState<TenantOpt[]>([]);
  useEffect(() => {
    authCall<{ tenants: TenantOpt[] }>("auth/account", undefined, "GET").then((r) => setTenants(r.tenants)).catch(() => undefined);
  }, []);
  if (tenants.length < 2) return null;
  return (
    <select aria-label="Switch brokerage" className="tenant-switcher" data-testid={testid}
      value={tenants.find((t) => t.current)?.tenant_id}
      onChange={async (e) => window.location.assign(await switchTenant(e.target.value))}>
      {tenants.map((t) => <option key={t.tenant_id} value={t.tenant_id}>{t.name}</option>)}
    </select>
  );
}

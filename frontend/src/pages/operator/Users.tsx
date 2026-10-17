import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { AssignedAe } from "./AssignedAe";

interface U {
  user_id: string; name: string; email: string; kind: "staff" | "client"; role: string; client: string | null;
  status: string; mfa: boolean; google: boolean; editable: boolean; open_invite_id: string | null;
}
interface Data { users: U[]; clients: { client_id: string; name: string }[]; staff_roles: string[] }
const CLIENT_ROLES = ["owner", "bookkeeper"];

export function Users() {
  const [d, setD] = useState<Data | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const load = () => authCall<Data>("o/users", undefined, "GET").then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function act(label: string, path: string, body?: unknown) {
    setErr(null); setMsg(null); setLink(null);
    try {
      const r = await authCall<{ invite_url?: string }>(path, body);
      if (r.invite_url) setLink(r.invite_url);
      setMsg(label); load();
    } catch (e: any) { setErr(e.message); }
  }
  if (!d) return <p data-testid="users-loading">{err || "Loading people…"}</p>;
  return (
    <section className="ledger-page" data-testid="users-page" style={{ display: "grid", gap: 16 }}>
      <h1 className="ledger-page__title">People</h1>
      <p><Link to="/o/invites" className="k-btn" data-testid="users-invite-link">Invite someone</Link></p>
      {msg ? <p className="brokaid-form__msg" data-testid="users-msg">{msg}</p> : null}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="users-error">{err}</p> : null}
      {link ? (
        <div className="brokaid-devbox" data-testid="users-link-box">
          <code data-testid="users-link-url">{link}</code>
          <button type="button" onClick={() => navigator.clipboard?.writeText(link)} data-testid="users-link-copy">Copy invite link</button>
        </div>
      ) : null}
      <table className="k-table" data-testid="users-table">
        <thead><tr><th>Person</th><th>Role</th><th>Status</th><th>Sign-in</th><th>Actions</th></tr></thead>
        <tbody>{d.users.map((u) => <Row key={u.user_id} u={u} roles={u.kind === "client" ? CLIENT_ROLES : d.staff_roles} act={act} />)}</tbody>
      </table>
      <h2 style={{ fontSize: 16 }}>Client account executives</h2>
      <div data-testid="users-clients" style={{ display: "grid", gap: 4 }}>
        {d.clients.map((c) => (
          <div key={c.client_id} data-testid={`users-client-${c.client_id}`}>
            <strong>{c.name}</strong>
            <AssignedAe clientId={c.client_id} />
          </div>
        ))}
      </div>
    </section>
  );
}

function Row({ u, roles, act }: { u: U; roles: string[]; act: (label: string, path: string, body?: unknown) => void }) {
  const base = `o/users/${u.user_id}`;
  const t = (k: string) => `user-${k}-${u.user_id}`;
  return (
    <tr data-testid={t("row")}>
      <td>{u.name}<br /><small>{u.email}{u.client ? ` · ${u.client}` : ""}</small></td>
      <td>
        {u.editable ? (
          <select value={u.role} data-testid={t("role")} onChange={(e) => act("Role changed.", `${base}/role`, { role: e.target.value })}>
            {[u.role, ...roles.filter((r) => r !== u.role)].map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        ) : u.role}
      </td>
      <td data-testid={t("status")}>{u.status}</td>
      <td><small>{u.mfa ? "Authenticator on" : "No authenticator"}{u.google ? " · Google" : ""}</small></td>
      <td style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {u.editable && (
          <>
            {u.open_invite_id && <button className="k-btn" data-testid={t("resend")} onClick={() => act("New invite link issued.", `o/invites/${u.open_invite_id}/reissue`)}>New invite link</button>}
            {u.status === "disabled"
              ? <button className="k-btn" data-testid={t("enable")} onClick={() => act("Enabled.", `${base}/enable`)}>Enable</button>
              : <button className="k-btn" data-testid={t("disable")} onClick={() => act("Disabled and signed out.", `${base}/disable`)}>Disable</button>}
            {u.mfa && <button className="k-btn" data-testid={t("reset-mfa")} onClick={() => act("Authenticator reset. They'll set it up next sign-in.", `${base}/reset-mfa`)}>Reset authenticator</button>}
            <button className="k-btn" data-testid={t("revoke")} onClick={() => act("Signed out everywhere.", `${base}/revoke-sessions`)}>Sign out</button>
          </>
        )}
      </td>
    </tr>
  );
}

import { useEffect, useState } from "react";
import { authCall } from "../../lib/authApi";

interface Invite { invite_id: string; email: string; name: string; role_label: string; state: string; expires_at: string }
interface Opts { invites: Invite[]; clients: { client_id: string; name: string }[]; roles: { value: string; label: string }[] }
const CLIENT_ROLES = ["owner", "bookkeeper"];
const EMAIL_NOTE: Record<string, string> = {
  sent: "Invite email sent.",
  skipped: "No email sent (address not on this brokerage's allow-list, or email is off). Copy the link and share it securely.",
  failed: "The invite email failed to send. Copy the link and share it securely.",
};

export function Invites() {
  const [opts, setOpts] = useState<Opts | null>(null);
  const [form, setForm] = useState({ email: "", name: "", role: "ae", client_id: "" });
  const [link, setLink] = useState<{ url: string; note: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const load = () => authCall<Opts>("o/invites", undefined, "GET").then(setOpts).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const isClient = CLIENT_ROLES.includes(form.role);

  async function act(path: string, body?: unknown) {
    setErr(null); setCopied(false);
    try {
      const r = await authCall<{ invite_url?: string; email_status?: string }>(path, body);
      if (r.invite_url) setLink({ url: r.invite_url, note: EMAIL_NOTE[r.email_status || "skipped"] || EMAIL_NOTE.skipped });
      load();
    } catch (e: any) { setErr(e.message); }
  }
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    act("o/invites", { ...form, client_id: isClient ? form.client_id : null });
  };
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <section className="ledger-page" data-testid="invites-page" style={{ display: "grid", gap: 20, maxWidth: 760 }}>
      <h1 className="ledger-page__title">Invite people</h1>
      <form onSubmit={submit} className="brokaid-form" style={{ maxWidth: 480 }} data-testid="invite-form">
        <label className="brokaid-form__label">Email<input type="email" required value={form.email} onChange={set("email")} data-testid="invite-form-email" /></label>
        <label className="brokaid-form__label">Name<input value={form.name} onChange={set("name")} data-testid="invite-form-name" /></label>
        <label className="brokaid-form__label">Role
          <select value={form.role} onChange={set("role")} data-testid="invite-form-role" className="invite-select">
            {opts?.roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        {isClient && (
          <label className="brokaid-form__label">Client
            <select required value={form.client_id} onChange={set("client_id")} data-testid="invite-form-client" className="invite-select">
              <option value="">Pick a client…</option>
              {opts?.clients.map((c) => <option key={c.client_id} value={c.client_id}>{c.name}</option>)}
            </select>
          </label>
        )}
        <button type="submit" data-testid="invite-form-submit">Create invite</button>
      </form>
      {err ? <p className="brokaid-form__err" role="alert" data-testid="invite-form-error">{err}</p> : null}
      {link && (
        <div className="brokaid-devbox" data-testid="invite-link-box">
          <strong>Invite link (works once, for 72 hours)</strong>
          <code data-testid="invite-link-url">{link.url}</code>
          <span data-testid="invite-email-status">{link.note}</span>
          <button type="button" data-testid="invite-link-copy"
            onClick={async () => { await navigator.clipboard?.writeText(link.url).catch(() => undefined); setCopied(true); }}>
            {copied ? "Copied" : "Copy invite link"}
          </button>
        </div>
      )}
      <InviteList invites={opts?.invites || []} act={act} />
    </section>
  );
}

function InviteList({ invites, act }: { invites: Invite[]; act: (p: string) => void }) {
  return (
    <table className="k-table" data-testid="invite-list">
      <thead><tr><th>Person</th><th>Role</th><th>Status</th><th /></tr></thead>
      <tbody>
        {invites.map((i) => (
          <tr key={i.invite_id} data-testid={`invite-row-${i.invite_id}`}>
            <td>{i.name}<br /><small>{i.email}</small></td>
            <td>{i.role_label}</td>
            <td data-testid={`invite-state-${i.invite_id}`}>{i.state}</td>
            <td>
              {i.state !== "accepted" && i.state !== "revoked" ? (
                <>
                  <button className="k-btn" onClick={() => act(`o/invites/${i.invite_id}/reissue`)} data-testid={`invite-reissue-${i.invite_id}`}>New link</button>{" "}
                  <button className="k-btn" onClick={() => act(`o/invites/${i.invite_id}/revoke`)} data-testid={`invite-revoke-${i.invite_id}`}>Cancel</button>
                </>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

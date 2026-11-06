/** R3.12 J6 — People on this account: who can do what; owners invite with a preset, change rights, remove. */
import { useCallback, useEffect, useState } from "react";
import { authCall } from "../../lib/authApi";
import { Copy } from "./api";

interface Member { membership_id: string; name: string; email: string | null; me: boolean; role: string; role_label: string; preset: string; can: string;
  rights: Record<string, boolean> }
interface Data { members: Member[]; invites: { invite_id: string; name: string; email: string; can: string }[]; can_manage: boolean }

const PRESETS: [string, string, string][] = [["bookkeeper", "Bookkeeper", "View and pay"], ["co_owner", "Co-owner or spouse", "Everything"],
  ["read_only", "Read only", "View cover and documents"], ["custom", "Custom", "Pick what they can do"]];
const TOGGLES: [string, string][] = [["can_pay", "Pay"], ["can_lodge_claim", "Make claims"], ["can_request_change", "Ask for changes"],
  ["can_accept", "Accept renewals and changes"]];

function Toggles({ v, set, id }: { v: Record<string, boolean>; set: (v: Record<string, boolean>) => void; id: string }) {
  return <>{TOGGLES.map(([k, l]) => (
    <label key={k}><input type="checkbox" checked={!!v[k]} onChange={(e) => set({ ...v, [k]: e.target.checked })} data-testid={`${id}-${k}`} />{l}</label>
  ))}</>;
}

function InviteForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ name: "", email: "", preset: "bookkeeper" });
  const [custom, setCustom] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const send = async () => {
    setBusy(true); setErr(null);
    try { setRes(await authCall("c/people/invite", { ...f, custom: f.preset === "custom" ? custom : null })); onDone(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  if (res) return (
    <div className="mer-form" data-testid="people-invite-result">
      {res.email_sent ? <p style={{ margin: 0 }} data-testid="people-invite-sent">Invite emailed. The link works once and expires in 72 hours.</p> : <>
        <p style={{ margin: 0 }} data-testid="people-invite-no-email">{res.email_note}</p>
        <Copy text={res.invite_link} label="invite link" testid="people-invite-copy" />
        <p className="mer-row__s" style={{ margin: 0 }}>The link works once and expires in 72 hours. They'll set a password and an authenticator app.</p></>}
      <button type="button" className="mer-btn mer-btn--ghost" onClick={() => setRes(null)} data-testid="people-invite-another">Invite someone else</button>
    </div>
  );
  return (
    <form className="mer-form" onSubmit={(e) => { e.preventDefault(); void send(); }} data-testid="people-invite-form">
      <input required placeholder="Their name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} data-testid="people-invite-name" />
      <input required type="email" placeholder="Their email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} data-testid="people-invite-email" />
      {PRESETS.map(([k, l, d]) => (
        <label key={k}><input type="radio" name="preset" checked={f.preset === k} onChange={() => setF({ ...f, preset: k })} data-testid={`people-preset-${k}`} />
          <span><b>{l}</b> · {d}</span></label>
      ))}
      {f.preset === "custom" ? <Toggles v={custom} set={setCustom} id="people-custom" /> : null}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="people-invite-error">{err}</p> : null}
      <button className="mer-btn" disabled={busy} data-testid="people-invite-send">{busy ? "Sending…" : "Send invite"}</button>
    </form>
  );
}

function MemberRow({ m, manage, reload }: { m: Member; manage: boolean; reload: () => void }) {
  const [edit, setEdit] = useState(false);
  const [preset, setPreset] = useState(m.preset === "co_owner" ? "bookkeeper" : m.preset);
  const [custom, setCustom] = useState(m.rights);
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); setEdit(false); reload(); } catch (e: any) { setErr(e.message); } };
  const can = manage && !m.me && m.role !== "owner";
  return (
    <div className="mer-row" style={{ flexWrap: "wrap" }} data-testid={`people-member-${m.membership_id}`}>
      <span className="mer-row__main"><p className="mer-row__t">{m.name}{m.me ? " (you)" : ""} · {m.role_label}</p>
        <p className="mer-row__s" data-testid={`people-can-${m.membership_id}`}>{m.can}</p></span>
      {can && !edit ? <button type="button" className="mer-btn mer-btn--ghost" onClick={() => setEdit(true)} data-testid={`people-edit-${m.membership_id}`}>Change</button> : null}
      {edit ? (
        <div className="mer-form" style={{ width: "100%" }}>
          {PRESETS.filter(([k]) => k !== "co_owner").map(([k, l, d]) => (
            <label key={k}><input type="radio" checked={preset === k} onChange={() => setPreset(k)} data-testid={`people-edit-preset-${k}`} /><span><b>{l}</b> · {d}</span></label>
          ))}
          {preset === "custom" ? <Toggles v={custom} set={setCustom} id="people-edit-custom" /> : null}
          <button type="button" className="mer-btn" onClick={() => run(() => authCall(`c/people/${m.membership_id}/rights`, { preset, custom: preset === "custom" ? custom : null }, "PUT"))}
            data-testid={`people-save-${m.membership_id}`}>Save</button>
          <button type="button" className="mer-btn mer-btn--ghost" style={{ color: "var(--bad)" }}
            onClick={() => window.confirm(`Remove ${m.name}? They'll lose access straight away.`) && run(() => authCall(`c/members/${m.membership_id}/remove`, {}))}
            data-testid={`people-remove-${m.membership_id}`}>Remove from this account</button>
        </div>
      ) : null}
      {err ? <p className="brokaid-form__err" role="alert" style={{ width: "100%" }}>{err}</p> : null}
    </div>
  );
}

export function People() {
  const [d, setD] = useState<Data | null>(null);
  const [inviting, setInviting] = useState(false);
  const load = useCallback(() => { authCall<Data>("c/people", undefined, "GET").then(setD).catch(() => setD(null)); }, []);
  useEffect(load, [load]);
  if (!d) return null;
  return (
    <section className="mer-sec" data-testid="people-section">
      <h2 className="mer-sec__title">People on this account</h2>
      <div className="mer-card">
        {d.members.map((m) => <MemberRow key={m.membership_id} m={m} manage={d.can_manage} reload={load} />)}
        {d.invites.map((i) => (
          <div key={i.invite_id} className="mer-row" data-testid={`people-invite-${i.invite_id}`}>
            <span className="mer-row__main"><p className="mer-row__t">{i.name} · invited</p><p className="mer-row__s">{i.email} · {i.can}</p></span>
            <button type="button" className="mer-btn mer-btn--ghost" onClick={() => authCall(`c/people/invites/${i.invite_id}/cancel`, {}).then(load)}
              data-testid={`people-invite-cancel-${i.invite_id}`}>Cancel</button>
          </div>
        ))}
        {d.can_manage ? (inviting ? <InviteForm onDone={load} /> : (
          <button type="button" className="mer-row" style={{ width: "100%", border: 0, background: "none", textAlign: "left", cursor: "pointer", font: "inherit" }}
            onClick={() => setInviting(true)} data-testid="people-invite-open"><span className="mer-row__main"><p className="mer-row__t">+ Invite someone</p></span></button>
        )) : <p className="mer-row__s" style={{ padding: "0 14px 14px" }} data-testid="people-readonly">Only an owner can add or change people.</p>}
      </div>
    </section>
  );
}

/** R0.11A — full alerts page + notification preferences (/c/alerts and /o/alerts). */
import { useEffect, useState } from "react";
import { useAlertsStore } from "../../state/alertsStore";
import { AlertsList, GroupedAlerts } from "./AlertsList";
import { PushControl } from "./PushControl";
import { PushTestCard } from "./PushTestCard";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface Prefs { in_app: boolean; email: boolean; push: boolean; documents_email?: boolean; quiet_hours: { start: string; end: string } }

function Toggle({ id, label, on, note, onChange }: { id: string; label: string; on: boolean; note: string; onChange?: (v: boolean) => void }) {
  return (
    <li className="brokaid-pref" data-testid={`pref-${id}`}>
      <label htmlFor={`pref-${id}-input`}>
        <span className="brokaid-pref__label">{label}</span>
        <span className="brokaid-pref__note">{note}</span>
      </label>
      <input id={`pref-${id}-input`} type="checkbox" role="switch" checked={on} disabled={!onChange}
        onChange={(e) => onChange?.(e.target.checked)} readOnly={!onChange}
        aria-describedby={`pref-${id}-note`} data-testid={`pref-${id}-toggle`} />
    </li>
  );
}

function PrefsCard({ surface }: { surface: "client" | "operator" }) {
  const [p, setP] = useState<Prefs | null>(null);
  useEffect(() => {
    void fetch(`${API}/api/notifications/prefs`, { credentials: "include" }).then((r) => (r.ok ? r.json() : null)).then(setP);
  }, []);
  if (!p) return null;
  const save = async (patch: Partial<Prefs>) => {
    const r = await fetch(`${API}/api/notifications/prefs`, {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: p.email, push: p.push, ...patch }),
    });
    if (r.ok) setP(await r.json());
  };
  const setEmail = (email: boolean) => save({ email });
  return (
    <section id="settings" className="brokaid-alerts__prefs" data-testid="alerts-prefs">
      <h2>Notification settings</h2>
      <ul>
        <Toggle id="in-app" label="In the app" on note="Always on" />
        <Toggle id="email" label="Email" on={p.email} onChange={setEmail}
          note={p.email ? "On. We email you about new alerts." : "Off. Turn on to get alerts by email."} />
        {surface === "client" ? (
          <Toggle id="documents-email" label="Documents by email" on={p.documents_email !== false}
            onChange={(documents_email) => save({ documents_email })}
            note={p.documents_email !== false ? "On. We email you documents like your renewal options PDF." : "Off. Documents stay in the app only."} />
        ) : null}
        <PushControl available={!!(p as any).push_available} />
      </ul>
      <p className="brokaid-pref__note" data-testid="alerts-quiet-hours">
        Emails and pushes are held between {p.quiet_hours.start} and {p.quiet_hours.end} Sydney time and sent at {p.quiet_hours.end}. In-app alerts are never held.
      </p>
    </section>
  );
}

export function AlertsPage({ surface }: { surface: "client" | "operator" }) {
  const { items, count, load, markAll } = useAlertsStore();
  useEffect(() => { void load(100); }, [load, count]);
  useEffect(() => {
    if (window.location.hash === "#settings") document.getElementById("settings")?.scrollIntoView();
  }, [items.length]);
  return (
    <section className={`brokaid-alerts brokaid-alerts--${surface}`} data-testid="alerts-page">
      <header className="brokaid-alerts__head">
        <div>
          <p className="brokaid-alerts__eyebrow">Alerts</p>
          <h1 className="brokaid-alerts__title">Alerts</h1>
        </div>
        <button type="button" className="brokaid-bell__link" onClick={() => void markAll()} disabled={!count}
          data-testid="alerts-mark-all">Mark all read</button>
      </header>
      {surface === "operator" ? <GroupedAlerts items={items} testid="alerts-list" /> : <AlertsList items={items} testid="alerts-list" />}
      <PrefsCard surface={surface} />
      {surface === "operator" ? <PushTestCard /> : null}
    </section>
  );
}

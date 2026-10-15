/** R0.11A — full alerts page + notification preferences (/c/alerts and /o/alerts). */
import { useEffect, useState } from "react";
import { useAlertsStore } from "../../state/alertsStore";
import { AlertsList } from "./AlertsList";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface Prefs { in_app: boolean; email: boolean; push: boolean; quiet_hours: { start: string; end: string } }

function Toggle({ id, label, on, note }: { id: string; label: string; on: boolean; note: string }) {
  return (
    <li className="brokaid-pref" data-testid={`pref-${id}`}>
      <label htmlFor={`pref-${id}-input`}>
        <span className="brokaid-pref__label">{label}</span>
        <span className="brokaid-pref__note">{note}</span>
      </label>
      <input id={`pref-${id}-input`} type="checkbox" role="switch" checked={on} disabled readOnly
        aria-describedby={`pref-${id}-note`} data-testid={`pref-${id}-toggle`} />
    </li>
  );
}

function PrefsCard() {
  const [p, setP] = useState<Prefs | null>(null);
  useEffect(() => {
    void fetch(`${API}/api/notifications/prefs`, { credentials: "include" }).then((r) => (r.ok ? r.json() : null)).then(setP);
  }, []);
  if (!p) return null;
  return (
    <section id="settings" className="brokaid-alerts__prefs" data-testid="alerts-prefs">
      <h2>Notification settings</h2>
      <ul>
        <Toggle id="in-app" label="In the app" on note="Always on" />
        <Toggle id="email" label="Email" on={p.email} note="Coming soon" />
        <Toggle id="push" label="Push" on={p.push} note="Coming soon" />
      </ul>
      <p className="brokaid-pref__note" data-testid="alerts-quiet-hours">
        When email and push arrive, they will hold between {p.quiet_hours.start} and {p.quiet_hours.end} Sydney time. In-app alerts are never held.
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
      <AlertsList items={items} testid="alerts-list" />
      <PrefsCard />
    </section>
  );
}

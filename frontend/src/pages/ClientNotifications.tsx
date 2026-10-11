/**
 * Phase 13 — client Notifications tray (bell in profile / accessible via /c/notifications).
 *
 * Reads `proj_notifications` via GET /api/c/notifications. Each row's
 * body_text matches the corresponding EmailRenderedDevMode body — same
 * source event.
 */
import { useEffect, useState } from "react";
import "../styles/phase13.css";
import { fmtDateTime } from "../lib/format";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface Notif {
  notification_id: string;
  ts: string;
  kind: string;
  subject: string;
  body_text: string;
  read: boolean;
  source_event_id: string;
}

export function ClientNotifications() {
  const [items, setItems] = useState<Notif[] | null>(null);
  useEffect(() => {
    fetch(`${API}/api/c/notifications`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => setItems(d.notifications || []))
      .catch(() => setItems([]));
  }, []);
  return (
    <section className="brokaid-tab" data-testid="tab-notifications-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Inbox</p>
        <h1 className="brokaid-tab__title">Notifications</h1>
      </header>
      {items && items.length === 0 ? (
        <p data-testid="notifications-empty" className="brokaid-empty-caption">
          Nothing here yet — updates on your claims and quotes will land here.
        </p>
      ) : null}
      <ul className="brokaid-notif-list" data-testid="notifications-list">
        {(items || []).map((n) => (
          <li
            key={n.notification_id}
            className="brokaid-notif"
            data-testid={`notif-${n.kind}`}
          >
            <div className="brokaid-notif__row">
              <span className="brokaid-notif__kind">{n.kind}</span>
              <span className="brokaid-notif__ts">
                {fmtDateTime(n.ts)}
              </span>
            </div>
            <h3 className="brokaid-notif__subject">{n.subject}</h3>
            <pre className="brokaid-notif__body">{n.body_text}</pre>
          </li>
        ))}
      </ul>
    </section>
  );
}

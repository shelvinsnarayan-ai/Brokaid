/** R0.11A — alerts list (used by the bell panel and the /c/alerts, /o/alerts pages). */
import { useNavigate } from "react-router-dom";
import { ago, stamp, useAlertsStore, type Alert } from "../../state/alertsStore";

function Row({ a, onOpen }: { a: Alert; onOpen: (a: Alert) => void }) {
  return (
    <li>
      <button type="button" className={`brokaid-alert${a.read ? "" : " is-unread"}`} onClick={() => onOpen(a)}
        data-testid={`alert-${a.id}`} data-unread={a.read ? undefined : "true"}>
        {!a.read && <span className="brokaid-sr-only">Unread</span>}
        <span className="brokaid-alert__title">{a.title}</span>
        <span className="brokaid-alert__body">{a.body}</span>
        <span className="brokaid-alert__time">{ago(a.updated_at)} · {stamp(a.updated_at)}</span>
      </button>
    </li>
  );
}

export function AlertsList({ items, onNavigate, testid }: { items: Alert[]; onNavigate?: () => void; testid: string }) {
  const navigate = useNavigate();
  const markRead = useAlertsStore((s) => s.markRead);
  const open = async (a: Alert) => {
    if (!a.read) await markRead(a.id);
    onNavigate?.();
    navigate(a.link);
  };
  if (!items.length) return <p className="brokaid-alerts__empty" data-testid={`${testid}-empty`}>No alerts yet.</p>;
  return (
    <ul className="brokaid-alerts__list" data-testid={testid}>
      {items.map((a) => <Row key={a.id} a={a} onOpen={open} />)}
    </ul>
  );
}

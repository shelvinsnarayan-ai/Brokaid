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

const SEV: [string, string][] = [["urgent", "Urgent"], ["action", "Needs action"], ["fyi", "For information"]];

/** R3.13: staff alerts grouped by client, then severity; collapsible with counts. */
export function GroupedAlerts({ items, testid }: { items: Alert[]; testid: string }) {
  const navigate = useNavigate();
  const markRead = useAlertsStore((s) => s.markRead);
  const open = async (a: Alert) => { if (!a.read) await markRead(a.id); navigate(a.link); };
  if (!items.length) return <p className="brokaid-alerts__empty" data-testid={`${testid}-empty`}>No alerts yet.</p>;
  const groups = new Map<string, Alert[]>();
  for (const a of items) {
    const k = a.client_name || "General";
    groups.set(k, [...(groups.get(k) || []), a]);
  }
  const keys = [...groups.keys()].sort((a, b) => (a === "General" ? 1 : b === "General" ? -1 : a.localeCompare(b)));
  return (
    <div data-testid={testid}>
      {keys.map((k, gi) => {
        const list = groups.get(k)!;
        const unread = list.filter((a) => !a.read).length;
        return (
          <details key={k} className="brokaid-alert-group" open={gi < 3 && unread > 0} data-testid={`${testid}-group-${gi}`}>
            <summary><strong>{k}</strong> · {list.length} alert{list.length === 1 ? "" : "s"}{unread ? ` · ${unread} unread` : ""}</summary>
            {SEV.map(([s, label]) => {
              const sl = list.filter((a) => (a.severity || "fyi") === s);
              return sl.length ? (
                <div key={s} data-testid={`${testid}-group-${gi}-${s}`}>
                  <p className="brokaid-alert-group__sev">{label} ({sl.length})</p>
                  <ul className="brokaid-alerts__list">{sl.map((a) => <Row key={a.id} a={a} onOpen={open} />)}</ul>
                </div>
              ) : null;
            })}
          </details>
        );
      })}
    </div>
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

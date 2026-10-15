/** R0.11A — bell with unread count; opens a small alerts panel. `base` is "/c" or "/o". */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../Icon";
import { useAlertsStore } from "../../state/alertsStore";
import { AlertsList } from "./AlertsList";

export function Bell({ base }: { base: "/c" | "/o" }) {
  const [open, setOpen] = useState(false);
  const { count, items, load, markAll } = useAlertsStore();
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    void load(10);
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [open, load]);
  const label = count ? `Alerts, ${count} unread` : "Alerts";
  return (
    <div className="brokaid-bell" ref={ref}>
      <button type="button" className="brokaid-bell__btn" onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog" aria-expanded={open} aria-label={label} data-testid="bell-button">
        <Icon name="bell" size={20} />
        {count > 0 && <span className="brokaid-bell__count" data-testid="bell-count">{count > 99 ? "99+" : count}</span>}
      </button>
      {open && (
        <div className="brokaid-bell__panel" role="dialog" aria-label="Alerts" data-testid="bell-panel">
          <div className="brokaid-bell__head">
            <strong>Alerts</strong>
            <button type="button" className="brokaid-bell__link" onClick={() => void markAll()} disabled={!count}
              data-testid="bell-mark-all">Mark all read</button>
          </div>
          <AlertsList items={items} onNavigate={() => setOpen(false)} testid="bell-list" />
          <div className="brokaid-bell__foot">
            <Link to={`${base}/alerts`} onClick={() => setOpen(false)} data-testid="bell-see-all">See all alerts</Link>
            <Link to={`${base}/alerts#settings`} onClick={() => setOpen(false)} data-testid="bell-settings">Notification settings</Link>
          </div>
        </div>
      )}
    </div>
  );
}

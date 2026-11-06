/** R3.15: shared "Access denied / not found" page + inline panel (both apps). Says what happened, who can help, Back and Home. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { messageFor } from "../lib/errors";

type Helper = { name: string; role: string; phone?: string | null; email?: string | null };

function useHelper(staff: boolean): Helper | null {
  const [h, setH] = useState<Helper | null>(null);
  useEffect(() => {
    const API = (import.meta as any).env.VITE_API_BASE || "";
    fetch(`${API}/api/${staff ? "o/help-contact" : "c/home"}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setH(staff ? d : d.broker ? { name: d.broker.name, role: "Your broker", phone: d.broker.phone, email: d.broker.email } : null))
      .catch(() => {});
  }, [staff]);
  return h;
}

export function AccessDenied({ status = 403, code, staff, inline, testid = "access-denied" }:
  { status?: number; code?: string; staff: boolean; inline?: boolean; testid?: string }) {
  const nav = useNavigate();
  const helper = useHelper(staff);
  const m = messageFor(status, code);
  const home = staff ? "/o/queue" : "/c";
  return (
    <section className={`brokaid-denied${inline ? " brokaid-denied--inline" : ""}`} role="alert" data-testid={testid}>
      <h1 className="brokaid-denied__title" data-testid={`${testid}-title`}>{m.title}</h1>
      <p className="brokaid-denied__next" data-testid={`${testid}-next`}>{m.next}</p>
      {helper ? (
        <p className="brokaid-denied__help" data-testid={`${testid}-helper`}>
          Who can help: <strong>{helper.name}</strong> ({helper.role})
          {helper.phone ? <> · <a href={`tel:${helper.phone}`} data-testid={`${testid}-call`}>{helper.phone}</a></> : null}
        </p>
      ) : null}
      <div className="brokaid-denied__actions">
        <button type="button" className="brokaid-btn brokaid-btn--secondary" onClick={() => nav(-1)} data-testid={`${testid}-back`}>Back</button>
        <a className="brokaid-btn brokaid-btn--primary" href={home} data-testid={`${testid}-home`}>Home</a>
      </div>
    </section>
  );
}

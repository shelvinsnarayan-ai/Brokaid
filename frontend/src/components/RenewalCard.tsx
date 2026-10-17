/** R1.6 — client "Renewal" card for Home and Cover. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "../styles/renewals.css";

const API = import.meta.env.VITE_API_BASE || "";

interface Row { renewal_id: string; product: string; expiry: string; stage: string; text: string; action_needed: boolean }

export function RenewalCards({ where }: { where: string }) {
  const [rows, setRows] = useState<Row[]>([]);
  useEffect(() => {
    fetch(`${API}/api/c/renewals`, { credentials: "include" }).then((r) => (r.ok ? r.json() : { renewals: [] }))
      .then((d) => setRows(d.renewals.filter((x: Row) => x.stage !== "declined"))).catch(() => {});
  }, []);
  if (!rows.length) return null;
  return (
    <div data-testid={`${where}-renewal-cards`}>
      {rows.map((r) => (
        <Link key={r.renewal_id} to={`/c/renewal/${r.renewal_id}`} className={`rn-c-card ${r.action_needed ? "rn-c-card--action" : ""}`}
          data-testid={`${where}-renewal-card-${r.renewal_id}`}>
          <span className="rn-c-card__eyebrow">Renewal · {r.product} · ends {r.expiry}</span>
          <span data-testid={`${where}-renewal-text-${r.renewal_id}`}>{r.text}</span>
          <span className="brokaid-next-action__cta">{r.action_needed ? "Continue →" : "See renewal →"}</span>
        </Link>
      ))}
    </div>
  );
}

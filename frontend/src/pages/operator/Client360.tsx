/**
 * /o/clients/:id — minimal Client 360 (header + paginated timeline).
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AssignedAe } from "./AssignedAe";
import { KEmpty } from "../../components/ledger/KEmpty";
import { KNumber } from "../../components/ledger/KNumber";
import { KTimeline } from "../../components/ledger/KTimeline";
import { PolicyDocs } from "./PolicyDocs";

const API = import.meta.env.VITE_API_BASE || "";
const PAGE = 25;

interface Header {
  client_id: string; display_name: string; industry: string;
  domicile: string; jurisdiction_code: "AU" | "SG";
  active_policies: number; open_matters: number;
}

interface Ev { event_id: string; seq: number; ts: string; type: string; actor: any; payload: any }

export function Client360() {
  const { id = "" } = useParams();
  const [header, setHeader] = useState<Header | null>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    (async () => {
      const r = await fetch(`${API}/api/o/client/${id}`, { credentials: "include" });
      if (r.ok) setHeader((await r.json()).client);
    })();
  }, [id]);

  useEffect(() => {
    (async () => {
      const r = await fetch(`${API}/api/o/client/${id}/timeline?limit=${PAGE}&offset=${offset}`, { credentials: "include" });
      if (!r.ok) return;
      const b = await r.json();
      setEvents((prev) => offset === 0 ? b.events : [...prev, ...b.events]);
      setTotal(b.total);
    })();
  }, [id, offset]);

  if (!header) return <KEmpty title="Loading Client 360" testId="client360-loading" />;

  return (
    <section data-testid="client360-page">
      <p className="ledger-main__eyebrow">Client · {header.jurisdiction_code}</p>
      <h1 className="ledger-main__title">{header.display_name}</h1>
      <AssignedAe clientId={id} />

      <div className="k-meta-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <div className="k-meta-cell"><div className="k-meta-cell__label">Industry</div>
          <div className="k-meta-cell__value" style={{ fontSize: 12 }}>{header.industry}</div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">Domicile</div>
          <div className="k-meta-cell__value" style={{ fontSize: 12 }}>{header.domicile}</div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">Active policies</div>
          <div className="k-meta-cell__value" data-testid="client360-policy-count"><KNumber value={header.active_policies} format="int" /></div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">Open matters</div>
          <div className="k-meta-cell__value" data-testid="client360-matter-count"><KNumber value={header.open_matters} format="int" /></div></div>
      </div>

      <PolicyDocs clientId={id} />

      <h2 style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 18, marginTop: 20 }}>
        Timeline · {total} events
      </h2>
      <KTimeline items={events} testId="client360-timeline" />
      {events.length < total && events.length > 5 && (
        <button
          className="k-btn"
          onClick={() => setOffset((o) => o + PAGE)}
          data-testid="client360-load-more"
          style={{ marginTop: 12 }}
        >
          Load more
        </button>
      )}

      <div style={{ marginTop: 24 }}>
        <Link to="/o/clients" className="k-btn">← Back to clients</Link>
      </div>
    </section>
  );
}

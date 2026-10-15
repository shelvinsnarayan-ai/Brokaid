/** R0.7 — /o/weekly-pack: plain headline numbers for the governing role (Principal / Operator). */
import { useEffect, useState } from "react";
import { API, Basis, PageHead, card, getJson, muted } from "./stubKit";

interface Metric { key: string; label: string; value: number; data_basis: string }
interface Pack { week_from: string; week_to: string; metrics: Metric[]; note: string }

export function WeeklyPack() {
  const [pack, setPack] = useState<Pack | null>(null);
  const [denied, setDenied] = useState(false);
  useEffect(() => {
    void getJson("/api/o/weekly-pack").then((r) => (r.ok ? setPack(r.data) : setDenied(true)));
  }, []);
  if (denied) return <p data-testid="weekly-pack-denied" style={muted}>The weekly pack is for the Principal.</p>;
  if (!pack) return <p data-testid="weekly-pack-loading" style={muted}>Loading…</p>;
  return (
    <div data-testid="weekly-pack-page" style={{ maxWidth: 760 }}>
      <PageHead eyebrow="PRINCIPAL · WEEKLY PACK" title="Weekly pack">
        <p style={muted} data-testid="weekly-pack-range">Week {pack.week_from} to {pack.week_to}</p>
      </PageHead>
      <section style={card} data-testid="weekly-pack-metrics">
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {pack.metrics.map((m) => (
            <li key={m.key} data-testid={`weekly-pack-${m.key}`}
              style={{ display: "flex", justifyContent: "space-between", padding: "10px 0", borderTop: "1px solid var(--line, #1E2E5A)" }}>
              <span>{m.label}</span>
              <span>
                <strong data-testid={`weekly-pack-${m.key}-value`} style={{ fontFamily: "'IBM Plex Mono', monospace" }}>{m.value}</strong>
                <Basis b={m.data_basis} testid={`weekly-pack-${m.key}-basis`} />
              </span>
            </li>
          ))}
        </ul>
        <p style={{ ...muted, marginTop: 12 }} data-testid="weekly-pack-note">{pack.note}</p>
      </section>
      <a className="k-btn k-btn--primary" href={`${API}/api/o/weekly-pack.pdf`} data-testid="weekly-pack-download">
        Download as PDF
      </a>
    </div>
  );
}

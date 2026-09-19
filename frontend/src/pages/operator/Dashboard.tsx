/**
 * Operator dashboard — KPI bar (12 tiles) + Daily Brief teaser + Queue teaser.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { KSpark } from "../../components/ledger/KSpark";
import { KNumber } from "../../components/ledger/KNumber";
import { KChip } from "../../components/ledger/KChip";
import { KEmpty } from "../../components/ledger/KEmpty";
import { deltaKind } from "../../theme/ledger";

const API = import.meta.env.VITE_API_BASE || "";
const DRILLABLE = new Set([5, 8, 10, 11]);

interface Kpi {
  kpi_id: number;
  scalar: number;
  meta: Record<string, any>;
  delta_prev: number | null;
  spark_series: number[];
}

interface BriefPayload {
  date: string; tenant: string | null;
  new_matters: { count: number };
  endorsements_in_flight: { count: number };
  claims_in_flight: { count: number };
  hitl_queue_size: number; overdue_hitl: number;
  kpis_of_note: { kpi_id: number; reason: string; delta: number }[];
}

interface Card {
  envelope_id: string;
  class_key: string;
  sla_state: "ok" | "warn" | "overdue";
  hitl_since: string;
  sla_deadline: string | null;
  gate_reason: string | null;
  confidence: number | null;
  client_display?: string | null;
}

function formatByUnit(k: Kpi): { text: string; format: "int" | "money" | "pct" | "seconds" | "raw" } {
  const unit = (k.meta?.unit as string | undefined) || "";
  if (unit === "%") return { text: `${k.scalar.toFixed(1)}%`, format: "pct" };
  if (unit === "seconds") return { text: "", format: "seconds" };
  if (unit === "AUD") return { text: "", format: "money" };
  if (unit === "days") return { text: "", format: "int" };
  if (unit.includes("tokens")) return { text: "", format: "int" };
  return { text: "", format: "raw" };
}

export function Dashboard() {
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [brief, setBrief] = useState<BriefPayload | null>(null);
  const [teaser, setTeaser] = useState<Card[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [k, b, q] = await Promise.all([
          fetch(`${API}/api/o/kpi/summary`, { credentials: "include" }),
          fetch(`${API}/api/o/daily_brief`, { credentials: "include" }),
          fetch(`${API}/api/o/queue`, { credentials: "include" }),
        ]);
        if (!k.ok) { setError(`kpi ${k.status}`); return; }
        setKpis((await k.json()).kpis);
        if (b.ok) setBrief(await b.json());
        if (q.ok) setTeaser(((await q.json()).cards || []).slice(0, 5));
      } catch (e) { setError(String(e)); }
    })();
  }, []);

  if (error) return <div className="k-empty" data-testid="dashboard-error">Error: {error}</div>;

  return (
    <section data-testid="operator-dashboard">
      <p className="ledger-main__eyebrow">Overview</p>
      <h1 className="ledger-main__title">Control tower</h1>

      <div className="k-kpi-grid" data-testid="kpi-grid">
        {kpis.map((k) => {
          const drillable = DRILLABLE.has(k.kpi_id);
          const nym = k.meta?.not_yet_measurable;
          const label = (k.meta?.label as string) || `KPI ${k.kpi_id}`;
          const fmt = formatByUnit(k);
          const d = deltaKind(k.delta_prev);
          const content = (
            <>
              <div className="k-tile__label">
                <span>{label}</span>
                {drillable && <span className="k-kbd" data-testid={`kpi-drill-${k.kpi_id}`}>drill</span>}
              </div>
              <div className={`k-tile__scalar ${nym ? "is-null" : ""}`} data-testid={`kpi-scalar-${k.kpi_id}`}>
                {nym ? "—" : (
                  <KNumber value={k.scalar} format={fmt.format} />
                )}
              </div>
              <div className={`k-tile__delta is-${d.kind}`} data-testid={`kpi-delta-${k.kpi_id}`}>
                {d.kind === "none" ? "no history" :
                 d.kind === "flat" ? "flat" :
                 <><KNumber value={Math.abs(d.value ?? 0)} format={fmt.format} muted /> {d.kind === "up" ? "↑" : "↓"}</>
                }
              </div>
              <KSpark series={k.spark_series} testId={`kpi-spark-${k.kpi_id}`} />
              {nym && <div className="k-tile__reason" title={k.meta?.reason}>{k.meta?.reason}</div>}
            </>
          );
          return drillable ? (
            <Link
              to={`/o/kpi/${k.kpi_id}`}
              key={k.kpi_id}
              className="k-tile is-drillable"
              data-testid={`kpi-tile-${k.kpi_id}`}
              aria-label={`Drill into ${label}`}
            >
              {content}
            </Link>
          ) : (
            <div key={k.kpi_id} className="k-tile" data-testid={`kpi-tile-${k.kpi_id}`}>
              {content}
            </div>
          );
        })}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <section data-testid="dashboard-brief">
          <h2 style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 18, marginTop: 0 }}>
            Daily Brief · {brief?.date ?? "—"}
          </h2>
          {brief ? (
            <div className="k-meta-grid">
              <div className="k-meta-cell"><div className="k-meta-cell__label">New matters (24h)</div>
                <div className="k-meta-cell__value" data-testid="brief-new-matters">{brief.new_matters.count}</div></div>
              <div className="k-meta-cell"><div className="k-meta-cell__label">Endorsements in flight</div>
                <div className="k-meta-cell__value" data-testid="brief-endorsements">{brief.endorsements_in_flight.count}</div></div>
              <div className="k-meta-cell"><div className="k-meta-cell__label">Claims in flight</div>
                <div className="k-meta-cell__value" data-testid="brief-claims">{brief.claims_in_flight.count}</div></div>
              <div className="k-meta-cell"><div className="k-meta-cell__label">HITL queue</div>
                <div className="k-meta-cell__value" data-testid="brief-hitl-size">
                  {brief.hitl_queue_size} <span style={{ color: "var(--copper-light)" }}>({brief.overdue_hitl} overdue)</span>
                </div></div>
            </div>
          ) : <KEmpty title="Loading" hint="…" />}
        </section>

        <section data-testid="dashboard-queue-teaser">
          <h2 style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 18, marginTop: 0 }}>
            Queue · top {teaser.length}
          </h2>
          {teaser.length === 0 ? (
            <KEmpty title="Nothing to review" hint="All caught up." />
          ) : (
            <table className="k-table">
              <thead>
                <tr><th>SLA</th><th>Class</th><th>Client</th><th>Confidence</th></tr>
              </thead>
              <tbody>
                {teaser.map((c) => (
                  <tr
                    key={c.envelope_id}
                    className={c.sla_state === "overdue" ? "is-overdue" : c.sla_state === "warn" ? "is-warn" : ""}
                    onClick={() => (window.location.href = `/o/queue/${c.envelope_id}`)}
                    data-testid={`teaser-row-${c.envelope_id}`}
                  >
                    <td><KChip variant={c.sla_state}>{c.sla_state}</KChip></td>
                    <td>{c.class_key}</td>
                    <td>{c.client_display ?? "—"}</td>
                    <td className="k-num"><KNumber value={c.confidence ?? null} format="raw" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div style={{ marginTop: 8 }}>
            <Link to="/o/queue" data-testid="teaser-open-queue" className="k-btn">Open full queue →</Link>
          </div>
        </section>
      </div>
    </section>
  );
}

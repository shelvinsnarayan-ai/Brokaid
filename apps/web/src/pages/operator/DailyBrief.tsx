/**
 * /o/brief — projection-only Daily Brief. Numbers + labels, no narrative.
 */
import { useEffect, useState } from "react";
import { KEmpty } from "../../components/ledger/KEmpty";
import { KNumber } from "../../components/ledger/KNumber";

const API = import.meta.env.VITE_API_BASE || "";

interface Brief {
  date: string; tenant: string | null;
  new_matters: { count: number; links: string[] };
  endorsements_in_flight: { count: number; links: string[] };
  claims_in_flight: { count: number; links: string[] };
  hitl_queue_size: number; overdue_hitl: number;
  kpis_of_note: { kpi_id: number; reason: string; delta: number }[];
}

const KPI_LABELS: Record<number, string> = {
  1: "Book", 2: "Income per client", 3: "Retention", 4: "Pipeline velocity",
  5: "Response time", 6: "Time to quote", 7: "Claims timeframe", 8: "Autonomous %",
  9: "Cost per matter", 10: "Approvals load", 11: "Compliance health", 12: "Cash",
};

export function DailyBrief() {
  const [brief, setBrief] = useState<Brief | null>(null);
  useEffect(() => {
    (async () => {
      const r = await fetch(`${API}/api/o/daily_brief`, { credentials: "include" });
      if (r.ok) setBrief(await r.json());
    })();
  }, []);
  if (!brief) return <KEmpty title="Loading Daily Brief" testId="brief-loading" />;
  return (
    <section data-testid="brief-page">
      <p className="ledger-main__eyebrow">Daily Brief</p>
      <h1 className="ledger-main__title">{brief.date} · {brief.tenant ?? "—"}</h1>
      <div className="k-meta-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        <div className="k-meta-cell"><div className="k-meta-cell__label">New matters</div>
          <div className="k-meta-cell__value" data-testid="brief-detail-new-matters">
            <KNumber value={brief.new_matters.count} format="int" />
          </div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">Endorsements in flight</div>
          <div className="k-meta-cell__value">
            <KNumber value={brief.endorsements_in_flight.count} format="int" />
          </div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">Claims in flight</div>
          <div className="k-meta-cell__value">
            <KNumber value={brief.claims_in_flight.count} format="int" />
          </div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">HITL queue</div>
          <div className="k-meta-cell__value" data-testid="brief-detail-hitl-size">
            <KNumber value={brief.hitl_queue_size} format="int" />
          </div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">Overdue</div>
          <div className="k-meta-cell__value" data-testid="brief-detail-overdue">
            <span style={{ color: brief.overdue_hitl > 0 ? "var(--alert)" : "var(--verify)" }}>
              <KNumber value={brief.overdue_hitl} format="int" />
            </span>
          </div></div>
        <div className="k-meta-cell"><div className="k-meta-cell__label">KPIs of note</div>
          <div className="k-meta-cell__value">
            <KNumber value={brief.kpis_of_note.length} format="int" />
          </div></div>
      </div>

      <h2 style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 18, marginTop: 20 }}>KPIs of note</h2>
      {brief.kpis_of_note.length === 0 ? (
        <KEmpty title="No notable movement" hint="No KPI shifted by ≥ 10% since last snapshot." testId="brief-kpis-empty" />
      ) : (
        <table className="k-table" data-testid="brief-kpis-table">
          <thead><tr><th>KPI</th><th>Reason</th><th className="k-num-th">Delta</th></tr></thead>
          <tbody>
            {brief.kpis_of_note.map((k) => (
              <tr key={k.kpi_id}>
                <td>{KPI_LABELS[k.kpi_id] ?? `KPI ${k.kpi_id}`}</td>
                <td>{k.reason}</td>
                <td className="k-num"><KNumber value={k.delta} format="raw" delta={k.delta} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/**
 * /o/kpi/:id — KPI detail. Drillable → event list; else meta breakdown.
 */
import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { KEmpty } from "../../components/ledger/KEmpty";
import { KNumber } from "../../components/ledger/KNumber";
import { KSpark } from "../../components/ledger/KSpark";

const API = import.meta.env.VITE_API_BASE || "";
const DRILLABLE = new Set([5, 8, 10, 11]);

export function KpiDetail() {
  const { id = "" } = useParams();
  const kpiId = Number(id);
  const [summary, setSummary] = useState<any | null>(null);
  const [drill, setDrill] = useState<any | null>(null);

  useEffect(() => {
    (async () => {
      const r = await fetch(`${API}/api/o/kpi/summary`, { credentials: "include" });
      if (r.ok) {
        const body = await r.json();
        setSummary(body.kpis.find((k: any) => k.kpi_id === kpiId));
      }
      if (DRILLABLE.has(kpiId)) {
        const d = await fetch(`${API}/api/o/kpi/${kpiId}/events`, { credentials: "include" });
        if (d.ok) setDrill(await d.json());
      }
    })();
  }, [kpiId]);

  if (!summary) return <KEmpty title="Loading KPI" testId="kpi-detail-loading" />;
  const nym = summary.meta?.not_yet_measurable;

  return (
    <section data-testid="kpi-detail-page">
      <p className="ledger-main__eyebrow">KPI {kpiId}</p>
      <h1 className="ledger-main__title">{summary.meta?.label ?? `KPI ${kpiId}`}</h1>

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 32, alignItems: "center", marginBottom: 24 }}>
        <div>
          <div style={{ color: "var(--slate)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Today
          </div>
          <div style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 40 }} data-testid="kpi-detail-scalar">
            {nym ? "—" : <KNumber value={summary.scalar} format="raw" />}
          </div>
          {summary.meta?.unit && <div style={{ color: "var(--slate)", fontSize: 12 }}>{summary.meta.unit}</div>}
        </div>
        <div style={{ maxWidth: 480 }}>
          <div style={{ color: "var(--slate)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>
            Trailing 12
          </div>
          <div style={{ height: 60 }}>
            <KSpark series={summary.spark_series} testId="kpi-detail-spark" />
          </div>
        </div>
      </div>

      {nym && (
        <div className="k-empty" style={{ marginBottom: 16 }} data-testid="kpi-detail-nym">
          <strong>Not yet measurable.</strong> {summary.meta?.reason}
        </div>
      )}

      <h2 style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 18 }}>Meta</h2>
      <pre style={{
        background: "rgba(232,226,214,0.03)", border: "1px solid rgba(232,226,214,0.08)",
        padding: 12, borderRadius: 3, fontSize: 12, overflowX: "auto",
        fontFamily: "var(--font-mono)", color: "var(--bone)",
      }} data-testid="kpi-detail-meta">
        {JSON.stringify(
          Object.fromEntries(Object.entries(summary.meta).filter(([k]) => k !== "events")),
          null, 2
        )}
      </pre>

      {DRILLABLE.has(kpiId) ? (
        <>
          <h2 style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 18, marginTop: 24 }}>
            Underlying events ({drill?.count ?? 0})
          </h2>
          {drill && drill.events.length > 0 ? (
            <table className="k-table" data-testid="kpi-detail-events">
              <thead><tr><th>#</th><th>Event</th></tr></thead>
              <tbody>
                {drill.events.map((e: any, i: number) => (
                  <tr key={i}>
                    <td className="k-num">{i + 1}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                      {JSON.stringify(e).slice(0, 220)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <KEmpty title="No underlying events" hint="This KPI has data but no per-event drill for the current window." />
          )}
        </>
      ) : (
        <div className="k-empty" data-testid="kpi-detail-not-drillable">
          Not drillable in this build. Every operator KPI shows on the dashboard;
          drill is available for KPIs 5, 8, 10, 11 only.
        </div>
      )}

      <div style={{ marginTop: 20 }}>
        <Link to="/o" className="k-btn" data-testid="kpi-detail-back">← Back to overview</Link>
      </div>
    </section>
  );
}

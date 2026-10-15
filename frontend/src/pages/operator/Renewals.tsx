/** R0.7 — /o/renewals: flat list of in-force policies by expiry. No workflow actions yet. */
import { useEffect, useState } from "react";
import { Basis, PageHead, cell, getJson, muted } from "./stubKit";

interface Row {
  policy_id: string; client: string; policy: string; insurer: string; expiry: string;
  days_to_go: { value: number; data_basis: string }; expired: boolean; promise: string; next_step: string;
}

export function Renewals() {
  const [rows, setRows] = useState<Row[] | null>(null);
  useEffect(() => { void getJson("/api/o/renewals").then((r) => setRows(r.ok ? r.data.renewals : [])); }, []);
  if (!rows) return <p data-testid="renewals-loading" style={muted}>Loading…</p>;
  return (
    <div data-testid="renewals-page" style={{ maxWidth: 1100 }}>
      <PageHead eyebrow="WORK · RENEWALS" title="Renewals">
        <p style={muted} data-testid="renewals-count">{rows.length} in-force policies, soonest expiry first<Basis b="registry" /></p>
      </PageHead>
      {!rows.length ? <p style={muted} data-testid="renewals-empty">No in-force policies.</p> : (
        <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }} data-testid="renewals-table">
          <thead><tr style={{ textAlign: "left", ...muted }}>
            {["Client", "Policy", "Insurer", "Expiry", "Days to go", "Promise", "Next step"].map((h) => <th key={h} style={{ padding: "6px 10px" }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.policy_id} data-testid={`renewal-row-${r.policy_id}`}>
                <td style={cell}>{r.client}</td>
                <td style={cell}>{r.policy}</td>
                <td style={cell}>{r.insurer}</td>
                <td style={{ ...cell, fontFamily: "'IBM Plex Mono', monospace" }} data-testid={`renewal-expiry-${r.policy_id}`}>{r.expiry}</td>
                <td style={{ ...cell, fontFamily: "'IBM Plex Mono', monospace" }} data-testid={`renewal-days-${r.policy_id}`}>
                  {r.expired ? "Expired" : r.days_to_go.value}<Basis b={r.days_to_go.data_basis} />
                </td>
                <td style={cell}>{r.promise}</td>
                <td style={cell} data-testid={`renewal-next-${r.policy_id}`}>{r.next_step}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

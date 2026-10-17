import { useEffect, useState } from "react";
import { get, money, uploadStatement } from "./api";

export function Statements({ onImported }: { onImported: () => void }) {
  const [rows, setRows] = useState<any[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [needs, setNeeds] = useState<{ file: File; headers: string[]; fields: string[] } | null>(null);
  const [map, setMap] = useState<Record<string, string>>({});
  const load = () => get<{ statements: any[] }>("o/bank/statements").then((d) => setRows(d.statements)).catch(() => {});
  useEffect(() => { load(); }, []);
  async function send(file: File, mapping?: Record<string, string>) {
    setErr(null); setMsg(null);
    try {
      const r = await uploadStatement(file, mapping);
      if (r.needs_mapping) { setNeeds({ file, headers: r.headers, fields: r.fields }); return; }
      setNeeds(null);
      const s = r.statement;
      setMsg(r.duplicate_file ? `That exact file was already imported (${s.filename}). Nothing added.`
        : `${s.format.toUpperCase()} statement: ${s.new_txns} new line(s), ${s.duplicate_txns} already held.`);
      load(); onImported();
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <section className="imp-card" data-testid="bank-statements">
      <h2 className="imp-h2">Statements</h2>
      <p className="imp-muted">Upload a trust account export: CBA, NAB, Westpac or ANZ CSV, or OFX/QFX. Lines already held are never counted twice.</p>
      <label className="imp-drop"><input type="file" accept=".csv,.ofx,.qfx" data-testid="bank-file-input" onChange={(e) => e.target.files?.[0] && send(e.target.files[0])} /><span>Choose a statement file</span></label>
      {needs ? (
        <div className="imp-sheet" data-testid="bank-mapping">
          <p>We couldn't recognise this bank's layout. Match the columns:</p>
          {["date", "description", "amount", "debit", "credit", "balance", "reference"].map((f) => (
            <label key={f} className="imp-row">{f}
              <select value={map[f] || ""} onChange={(e) => setMap({ ...map, [f]: e.target.value })} data-testid={`bank-map-${f}`}>
                <option value="">—</option>{needs.headers.map((h) => <option key={h} value={h}>{h}</option>)}
              </select>
            </label>
          ))}
          <button className="k-btn k-btn--primary" onClick={() => send(needs.file, Object.fromEntries(Object.entries(map).filter(([, v]) => v)))} data-testid="bank-map-submit">Import with these columns</button>
        </div>
      ) : null}
      {msg ? <p className="brokaid-form__msg" data-testid="bank-upload-msg">{msg}</p> : null}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="bank-upload-error">{err}</p> : null}
      <table className="imp-table">
        <thead><tr><th>Period</th><th>File</th><th>Bank</th><th>Opening</th><th>Closing</th><th>Lines (new / held)</th><th>Running balance</th></tr></thead>
        <tbody>{rows.map((s) => {
          const gaps = (s.continuity.gaps || []).length + (s.continuity.gap_from_previous ? 1 : 0);
          return (
            <tr key={s.statement_id} data-testid={`bank-statement-${s.statement_id.slice(0, 8)}`}>
              <td>{s.period_from} → {s.period_to}</td><td>{s.filename}</td><td>{s.format.toUpperCase()}</td>
              <td>{s.opening ? money(s.opening) : "—"}</td><td>{s.closing ? money(s.closing) : "—"}</td><td>{s.new_txns} / {s.duplicate_txns}</td>
              <td className={gaps ? "imp-err" : ""} data-testid={`bank-continuity-${s.statement_id.slice(0, 8)}`}>
                {!s.continuity.checked && !s.continuity.gap_from_previous ? "No balances in this export" : gaps ? `${gaps} gap(s) — lines missing` : "Continuous"}
              </td>
            </tr>
          );
        })}</tbody>
      </table>
    </section>
  );
}

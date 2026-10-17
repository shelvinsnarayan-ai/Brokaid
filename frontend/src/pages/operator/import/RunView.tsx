import { useCallback, useEffect, useState } from "react";
import { api, Counts, ENTITY_LABEL, Entity, errorsCsvUrl, label, Row, Run } from "./api";

const fmt = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

function CountsTable({ counts }: { counts: Record<string, Counts> }) {
  return (
    <table className="imp-table" data-testid="import-counts">
      <thead><tr><th>Data</th><th>Rows</th><th>OK</th><th>Warnings</th><th>Errors</th><th>New</th><th>Changed</th><th>Unchanged</th></tr></thead>
      <tbody>
        {Object.entries(counts).map(([e, c]) => (
          <tr key={e} data-testid={`import-counts-${e}`}>
            <td>{ENTITY_LABEL[e as Entity] ?? e}</td><td>{c.rows}</td><td>{c.ok}</td>
            <td className={c.warn ? "imp-warn" : ""}>{c.warn}</td><td className={c.error ? "imp-err" : ""}>{c.error}</td>
            <td>{c.create}</td><td>{c.update}</td><td>{c.unchanged}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RowsTable({ runId }: { runId: string }) {
  const [filter, setFilter] = useState("status=error");
  const [d, setD] = useState<{ total: number; rows: Row[] } | null>(null);
  useEffect(() => { api(`o/import/runs/${runId}/rows?${filter}&limit=200`, undefined, "GET").then(setD).catch(() => setD(null)); }, [runId, filter]);
  const tabs = [["status=error", "Errors"], ["status=warn", "Warnings"], ["action=update", "Changes"], ["action=create", "New"], ["action=unchanged", "Unchanged"]];
  return (
    <div data-testid="import-rows">
      <div className="imp-tabs">
        {tabs.map(([q, l]) => (
          <button key={q} className={`k-btn ${filter === q ? "k-btn--primary" : ""}`} onClick={() => setFilter(q)}
                  data-testid={`import-rows-tab-${l.toLowerCase()}`}>{l}</button>
        ))}
        <span className="imp-muted" data-testid="import-rows-total">{d ? `${d.total} row(s)${d.total > 200 ? " · first 200 shown" : ""}` : ""}</span>
      </div>
      <table className="imp-table">
        <thead><tr><th>Sheet · row</th><th>Data</th><th>Key</th><th>Result</th><th>Why / what changes</th></tr></thead>
        <tbody>
          {(d?.rows ?? []).map((r) => (
            <tr key={r.row_id} data-testid={`import-row-${r.sheet}-${r.row}`.replace(/\s+/g, "-")}>
              <td>{r.sheet} · {r.row}</td><td>{ENTITY_LABEL[r.entity]}</td><td><code>{r.key ?? "—"}</code></td>
              <td className={r.status === "error" ? "imp-err" : r.status === "warn" ? "imp-warn" : ""}>{r.status === "error" ? "Error" : r.status === "warn" ? "Warning" : "OK"}{r.action ? ` · ${r.action}` : ""}</td>
              <td>
                {r.issues.map((i, k) => <div key={k}>{i.text}</div>)}
                {Object.entries(r.diff || {}).map(([f, [a, b]]) => (
                  <div key={f} className="imp-diff">{label(f)}: <s>{fmt(a)}</s> → <strong>{fmt(b)}</strong></div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Reconciliation({ runId }: { runId: string }) {
  const [d, setD] = useState<{ not_in_file: Record<string, { key: string; name: string }[]> } | null>(null);
  useEffect(() => { api(`o/import/runs/${runId}/reconciliation`, undefined, "GET").then(setD).catch(() => {}); }, [runId]);
  if (!d) return null;
  return (
    <div data-testid="import-reconciliation">
      <h3 className="imp-h3">Reconciliation</h3>
      {Object.entries(d.not_in_file).map(([e, list]) => (
        <p key={e} className="imp-muted" data-testid={`import-recon-${e}`}>
          {list.length === 0 ? `Every imported ${e} record is in this file.` : `${list.length} imported ${e} not in this file: ${list.slice(0, 8).map((x) => x.key).join(", ")}${list.length > 8 ? "…" : ""}`}
        </p>
      ))}
    </div>
  );
}

export function RunView({ runId, onDone }: { runId: string; onDone: () => void }) {
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<Run>(`o/import/runs/${runId}`, undefined, "GET").then(setRun).catch((e) => setErr(e.message)), [runId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (run?.status !== "committing") return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [run?.status, load]);
  useEffect(() => { if (run?.status === "committed") onDone(); }, [run?.status, onDone]);
  if (!run) return <p data-testid="import-run-loading">{err || "Loading…"}</p>;
  const todo = Object.values(run.counts).reduce((n, c) => n + c.create + c.update, 0);
  const errors = Object.values(run.counts).reduce((n, c) => n + c.error + c.warn, 0);
  async function commit() {
    setErr(null);
    try { await api(`o/import/runs/${runId}/commit`); load(); } catch (e: any) { setErr(e.message); }
  }
  const pct = run.progress.total ? Math.round((100 * run.progress.done) / run.progress.total) : 100;
  return (
    <section className="imp-card" data-testid="import-run-view">
      <h2 className="imp-h2">3 · Check and import · <span className="imp-muted">{run.filename}</span></h2>
      <p className="imp-muted">File fingerprint <code data-testid="import-run-sha">{run.file_sha.slice(0, 12)}</code> · status <strong data-testid="import-run-status">{run.status}</strong></p>
      <CountsTable counts={run.counts} />
      {errors ? <p><a className="k-btn" href={errorsCsvUrl(runId)} data-testid="import-errors-download">Download issue report (CSV)</a></p> : null}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="import-commit-error">{err}</p> : null}
      {run.status === "validated" ? (
        todo ? <button className="k-btn k-btn--primary" onClick={commit} data-testid="import-commit-button">Import {todo} new or changed record(s)</button>
             : <p data-testid="import-nothing-to-do">Nothing to change: everything in this file already matches what we hold.</p>
      ) : null}
      {run.status === "committing" || run.status === "committed" ? (
        <div className="imp-progress" data-testid="import-progress"><div style={{ width: `${pct}%` }} /><span>{run.progress.done} / {run.progress.total}</span></div>
      ) : null}
      {run.status === "committed" ? <p className="brokaid-form__msg" data-testid="import-committed-msg">Imported. Rows with errors were skipped; fix them in iAdviser and import again.</p> : null}
      <RowsTable runId={runId} />
      <Reconciliation runId={runId} />
    </section>
  );
}

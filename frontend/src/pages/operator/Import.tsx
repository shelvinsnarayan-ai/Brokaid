import { useCallback, useEffect, useState } from "react";
import { useOperatorStore } from "../../state/operatorStore";
import { api, Run } from "./import/api";
import { CleanStart } from "./import/CleanStart";
import { RunView } from "./import/RunView";
import { UploadStep } from "./import/UploadStep";
import "../../styles/import.css";

function History({ runs, open }: { runs: Run[]; open: (id: string) => void }) {
  const total = (r: Run, k: "create" | "update" | "error") => Object.values(r.counts).reduce((n, c) => n + c[k], 0);
  return (
    <section className="imp-card" data-testid="import-history">
      <h2 className="imp-h2">Import history</h2>
      {runs.length === 0 ? <p className="imp-muted" data-testid="import-history-empty">No imports yet.</p> : (
        <table className="imp-table">
          <thead><tr><th>When</th><th>Who</th><th>File</th><th>New</th><th>Changed</th><th>Errors</th><th>Status</th><th /></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id} data-testid={`import-history-row-${r.run_id.slice(0, 8)}`}>
                <td>{new Date(r.created_at).toLocaleString("en-AU")}</td><td>{r.created_by_name ?? "—"}</td><td>{r.filename}</td>
                <td>{total(r, "create")}</td><td>{total(r, "update")}</td><td>{total(r, "error")}</td><td>{r.status}</td>
                <td><button className="k-btn" onClick={() => open(r.run_id)} data-testid={`import-history-open-${r.run_id.slice(0, 8)}`}>Open</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function ImportPage() {
  const role = useOperatorStore((s) => s.session)?.session?.active_role;
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const load = useCallback(() => api<{ runs: Run[] }>("o/import/runs", undefined, "GET").then((d) => setRuns(d.runs)).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  if (role && !["operator", "principal"].includes(role)) {
    return <p data-testid="import-forbidden">Only the Operator or a Principal can import data.</p>;
  }
  return (
    <section className="ledger-page imp-page" data-testid="import-page">
      <h1 className="ledger-page__title">Import from iAdviser</h1>
      <p className="imp-muted">Bring clients, policies and vehicles across from an iAdviser report export. We never write back to iAdviser.</p>
      {runId ? (
        <>
          <button className="k-btn" onClick={() => setRunId(null)} data-testid="import-new-button">Start another import</button>
          <RunView runId={runId} onDone={load} />
        </>
      ) : <UploadStep onRun={(r) => { setRunId(r.run_id); load(); }} />}
      <History runs={runs} open={setRunId} />
      {role === "operator" ? <CleanStart /> : null}
    </section>
  );
}

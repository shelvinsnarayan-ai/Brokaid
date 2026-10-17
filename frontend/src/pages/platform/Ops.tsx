import { useState } from "react";
import { post, useLoad } from "../operator/compliance/api";

const pct = (v: number) => `${(v * 100).toFixed(2)}%`;

function Backups() {
  const { data, load } = useLoad<any>("platform/backups");
  const [msg, setMsg] = useState<string | null>(null);
  const run = (p: string) => post<any>(p, {}).then((r) => { setMsg(r.result ? `Drill ${r.result}${r.reason ? `: ${r.reason}` : ""}` : `Backed up ${r.backups?.length} tenant(s).`); load(); }).catch((e) => setMsg(e.message));
  return (
    <div className="imp-card" data-testid="ops-backups">
      <h2>Backups &amp; restore drills</h2>
      <button className="k-btn" onClick={() => void run("platform/backups/run")} data-testid="ops-backup-run">Back up all tenants now</button>
      {msg ? <p data-testid="ops-backup-msg">{msg}</p> : null}
      <table className="imp-table"><thead><tr><th>Tenant</th><th>When</th><th>Tier</th><th>Docs</th><th>Size</th><th>Key</th><th></th></tr></thead>
        <tbody>{(data?.backups || []).slice(0, 15).map((b: any) => (
          <tr key={b.backup_id} data-testid={`ops-backup-${b.backup_id}`}><td>{b.tenant_slug}</td><td>{new Date(b.at).toLocaleString("en-AU")}</td><td>{b.tier}{b.expired ? " (expired)" : ""}</td>
            <td>{Object.values(b.counts || {}).reduce((a: number, c: any) => a + c, 0) as number}</td><td>{Math.round(b.size / 1024)} KB</td><td>{b.key_id}</td>
            <td><button className="k-btn" onClick={() => void run(`platform/backups/drill/${b.tenant_id}`)} data-testid={`ops-drill-${b.tenant_slug}-${b.backup_id}`}>Restore drill</button></td></tr>))}</tbody></table>
      <h3>Drills</h3>
      <ul>{(data?.drills || []).slice(0, 10).map((d: any, i: number) => <li key={i} data-testid={`ops-drill-row-${i}`}>{new Date(d.at).toLocaleString("en-AU")} · {d.result} · {d.events ?? 0} events · chain {String(d.chain_ok)} · counts {String(d.counts_ok)}{d.reason ? ` · ${d.reason}` : ""}</li>)}</ul>
    </div>
  );
}

export function Ops() {
  const { data, err, load } = useLoad<any>("platform/ops");
  const [msg, setMsg] = useState<string | null>(null);
  if (err) return <p className="imp-err" role="alert" data-testid="ops-error">{err}</p>;
  if (!data) return <p className="imp-muted">Loading…</p>;
  const e = data.errors;
  return (
    <div className="imp-page" data-testid="ops-root">
      <div className="imp-card" data-testid="ops-summary">
        <h2>Health</h2>
        <p data-testid="ops-ready">Ready: <strong>{data.ready.ok ? "yes" : "NO"}</strong> — {Object.entries(data.ready.checks).map(([k, v]: any) => `${k} ${v.ok ? "ok" : "FAIL"}`).join(" · ")}</p>
        <p data-testid="ops-error-rate">Errors: {e.count_1h}/{e.requests_1h} in 1h ({pct(e.rate_1h)}) · {e.count_24h}/{e.requests_24h} in 24h ({pct(e.rate_24h)})</p>
        <p data-testid="ops-jobs">Jobs: {data.jobs.queued} queued · {data.jobs.dead} dead ({data.jobs.dead_over_1h} over 1h)</p>
        <p data-testid="ops-email">Email failures 24h: {data.email.failed}/{data.email.done} · Push failures 24h: {data.push.failed}/{data.push.done}</p>
        <p data-testid="ops-spend">AI spend this month (estimate): US${data.spend.usd_month} of US${data.spend.cap_usd} cap{data.spend.pct != null ? ` (${data.spend.pct}%)` : ""}</p>
        <p data-testid="ops-eval">Last eval: {data.eval?.summary || "none"}</p>
        <p data-testid="ops-release">Release checks: {data.release_checks.map((r: any) => `${r.slug} ${r.result || "never"}`).join(" · ")}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="k-btn" onClick={() => post("platform/ops/force-error", {}).catch(() => { setMsg("Forced error sent (expect a 500)."); load(); })} data-testid="ops-force-error">Force a test error</button>
          <button className="k-btn" onClick={() => post<any>("platform/ops/watch", {}).then((r) => setMsg(`Alert check: ${r.fired.join(", ") || "nothing to alert"}`))} data-testid="ops-watch">Run alert check</button>
          <button className="k-btn" onClick={load} data-testid="ops-refresh">Refresh</button>
        </div>
        {msg ? <p data-testid="ops-msg">{msg}</p> : null}
      </div>
      <div className="imp-card" data-testid="ops-slow"><h2>Slowest endpoints (p95, 24h)</h2>
        <table className="imp-table"><tbody>{data.slow_endpoints.map((s: any) => <tr key={s.route}><td>{s.route}</td><td>{s.p95_ms} ms</td><td>{s.n} calls</td></tr>)}</tbody></table></div>
      <div className="imp-card" data-testid="ops-errors"><h2>Errors</h2>
        <table className="imp-table"><thead><tr><th>Type</th><th>Route</th><th>Count</th><th>First</th><th>Last</th></tr></thead>
          <tbody>{e.recent.map((x: any) => <tr key={x.fingerprint} data-testid={`ops-err-${x.fingerprint}`}><td>{x.type}</td><td>{x.route}</td><td>{x.count}</td><td>{new Date(x.first_seen).toLocaleString("en-AU")}</td><td>{new Date(x.last_seen).toLocaleString("en-AU")}</td></tr>)}</tbody></table></div>
      <Backups />
    </div>
  );
}

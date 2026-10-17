import { useState } from "react";
import { api } from "./api";

interface Dry { dry_run_id: string; counts: Record<string, number>; ids_sha256: string; allowed: boolean; tenant: string; slug: string }
const NAMES: Record<string, string> = {
  clients: "clients", policies: "policies", things: "things (vehicles, places)", claims: "claims", memberships: "client role assignments",
  user_client_links: "client login links", user_brokerage_roles: "staff role assignments", users: "demo users",
};

export function CleanStart() {
  const [d, setD] = useState<Dry | null>(null);
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function dry() {
    setErr(null); setMsg(null);
    try { setD(await api<Dry>("o/clean-start/dry-run", undefined, "GET")); } catch (e: any) { setErr(e.message); }
  }
  async function run() {
    if (!d) return;
    setErr(null);
    try { await api("o/clean-start", { dry_run_id: d.dry_run_id, confirm }); setMsg("Archived. Nothing was deleted; the ledger records what was archived."); setD(null); }
    catch (e: any) { setErr(e.message); }
  }
  return (
    <section className="imp-card" data-testid="clean-start-card">
      <h2 className="imp-h2">Clean start (Operator)</h2>
      <p className="imp-muted">Archives this brokerage's demo records before real data is imported. Nothing is deleted: rows are flagged, demo role assignments are switched off, and one ledger entry records the counts.</p>
      <button className="k-btn" onClick={dry} data-testid="clean-start-dry-run">Dry run: show what would be archived</button>
      {d ? (
        <div data-testid="clean-start-result">
          <ul>{Object.entries(d.counts).map(([k, n]) => <li key={k} data-testid={`clean-start-count-${k}`}>{n} {NAMES[k] ?? k}</li>)}</ul>
          <p className="imp-muted">Fingerprint <code>{d.ids_sha256.slice(0, 12)}</code></p>
          {d.allowed ? (
            <div className="imp-row">
              <label>Type <code>{d.slug}</code> to confirm <input value={confirm} onChange={(e) => setConfirm(e.target.value)} data-testid="clean-start-confirm" /></label>
              <button className="k-btn k-btn--danger" disabled={confirm !== d.slug} onClick={run} data-testid="clean-start-execute">Archive now</button>
            </div>
          ) : <p data-testid="clean-start-not-approved">Clean start for {d.tenant} is waiting for the owner's approval. This was a dry run only.</p>}
        </div>
      ) : null}
      {msg ? <p className="brokaid-form__msg" data-testid="clean-start-msg">{msg}</p> : null}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="clean-start-error">{err}</p> : null}
    </section>
  );
}

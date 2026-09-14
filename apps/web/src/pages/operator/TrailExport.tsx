/**
 * Phase 15 — Operator trAIl export view.
 *
 * Client selector + date range → preview count → download zip bundle.
 * The downloaded zip verifies offline (Node 18+, `canonicalize` npm).
 */
import { useEffect, useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface ClientRow {
  client_id: string;
  display_name: string;
}

interface PreviewShape {
  event_count: number;
  event_types_summary: { type: string; count: number }[];
}

export function TrailExport() {
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [clientId, setClientId] = useState<string>("");
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [preview, setPreview] = useState<PreviewShape | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/o/clients`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { clients: [] }))
      .then((d) => setClients(d.clients || []))
      .catch(() => setClients([]));
  }, []);

  const qs = new URLSearchParams();
  if (clientId) qs.set("client_id", clientId);
  if (dateFrom) qs.set("from", dateFrom);
  if (dateTo) qs.set("to", dateTo);

  const runPreviewWith = async (from: string, to: string, cid: string) => {
    setBusy(true);
    setErr(null);
    try {
      const q = new URLSearchParams();
      if (cid) q.set("client_id", cid);
      if (from) q.set("from", from);
      if (to) q.set("to", to);
      const r = await fetch(`${API}/api/trail/export/preview?${q}`, {
        credentials: "include",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setPreview(await r.json());
    } catch (e: any) {
      setErr(String(e.message || e));
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const runPreview = () => runPreviewWith(dateFrom, dateTo, clientId);

  const setRangeDays = (days: number) => {
    const now = new Date();
    const then = new Date(now.getTime() - days * 24 * 3600 * 1000);
    const iso = (d: Date) =>
      `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
    const from = iso(then);
    const to = iso(now);
    setDateFrom(from);
    setDateTo(to);
    // Explicit-click model — same pathway as the Preview button.
    runPreviewWith(from, to, clientId);
  };

  const download = () => {
    window.location.href = `${API}/api/trail/export/bundle?${qs}`;
  };

  return (
    <section data-testid="trail-export-root">
      <p className="ledger-main__eyebrow">Ledger · export</p>
      <h1 className="ledger-main__title">Export a client ledger bundle</h1>
      <p className="te-lede">
        This bundle verifies without our server. Run
        {" "}<code>node verify.js export.json</code>{" "}
        on any Node 18+ machine.
      </p>

      <div className="te-controls">
        <div className="te-chip-row" role="group" aria-label="Range presets">
          <button
            type="button"
            className="te-chip"
            onClick={() => setRangeDays(7)}
            data-testid="te-chip-7d"
          >
            Last 7 days
          </button>
          <button
            type="button"
            className="te-chip"
            onClick={() => setRangeDays(30)}
            data-testid="te-chip-30d"
          >
            Last 30 days
          </button>
          <button
            type="button"
            className="te-chip"
            onClick={() => setRangeDays(90)}
            data-testid="te-chip-90d"
          >
            Last 90 days
          </button>
        </div>
        <label className="te-field">
          <span>Client</span>
          <select
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            data-testid="te-client"
          >
            <option value="">All clients in tenant</option>
            {clients.map((c) => (
              <option key={c.client_id} value={c.client_id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="te-field">
          <span>From</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            data-testid="te-from"
          />
        </label>
        <label className="te-field">
          <span>To</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            data-testid="te-to"
          />
        </label>
        <div className="te-actions">
          <button
            className="sm-btn"
            onClick={runPreview}
            disabled={busy}
            data-testid="te-preview"
          >
            {busy ? "Counting…" : "Preview event count"}
          </button>
          <button
            className="sm-btn sm-btn--active"
            onClick={download}
            disabled={!preview}
            data-testid="te-download"
          >
            Download ledger export bundle
          </button>
        </div>
      </div>

      {err ? <p className="sm-error" data-testid="te-error">Error: {err}</p> : null}

      {preview ? (
        <div className="te-preview" data-testid="te-preview-panel">
          <h2>
            <span className="te-count" data-testid="te-count">
              {preview.event_count.toLocaleString()}
            </span>
            <span className="te-count-label"> events in this range</span>
          </h2>
          <table className="sm-table">
            <thead>
              <tr><th>Type</th><th className="te-num">Count</th></tr>
            </thead>
            <tbody>
              {preview.event_types_summary.map((t) => (
                <tr key={t.type} data-testid={`te-type-${t.type}`}>
                  <td>{t.type}</td>
                  <td className="te-num">{t.count}</td>
                </tr>
              ))}
              {preview.event_types_summary.length === 0 ? (
                <tr><td colSpan={2} className="sm-empty">No events in range.</td></tr>
              ) : null}
            </tbody>
          </table>
          <p className="te-verified">
            <strong>Verified independently</strong> — the zip contains
            <code>export.json</code>, <code>verify.js</code>,
            <code>verifier_binary_sha256.txt</code>, and a{" "}
            <code>README.md</code>. Chain-proof matches the tenant genesis.
          </p>
        </div>
      ) : null}
    </section>
  );
}

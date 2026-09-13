/**
 * Phase 14 — Shadow Mode dashboard.
 *
 * /o/shadow-mode  → summary table (window / agent / class filters, export)
 * /o/shadow-mode/:agent/:class_key  → drill: paired decisions
 */
import { useEffect, useMemo, useState } from "react";
import { NavLink, useParams } from "react-router-dom";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface SummaryRow {
  agent_name: string;
  agent_display: string;
  class_key: string;
  class_display: string;
  decisions_seen: number;
  agreements: number;
  agreement_rate: number | null;
  eno_misses: number;
  unlocked: boolean;
  unlock_status: "locked" | "on_track" | "unlocked" | "relocked_recently";
  first_decision_at: string | null;
  last_decision_at: string | null;
}

type WindowKey = "7d" | "30d" | "all";
const WINDOWS: WindowKey[] = ["7d", "30d", "all"];

function rateClass(rate: number | null): string {
  if (rate === null) return "sm-rate sm-rate--none";
  if (rate >= 0.95) return "sm-rate sm-rate--verify";
  if (rate >= 0.8) return "sm-rate sm-rate--copper";
  return "sm-rate sm-rate--alert";
}

function statusClass(status: string): string {
  return `sm-status sm-status--${status}`;
}

export function ShadowMode() {
  const [rows, setRows] = useState<SummaryRow[]>([]);
  const [win, setWin] = useState<WindowKey>("30d");
  const [agents, setAgents] = useState<string[]>([]);
  const [classes, setClasses] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${API}/api/o/shadow-mode/summary?window=${win}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => { if (!cancelled) { setRows(d.rows || []); setError(null); } })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [win]);

  const agentOptions = useMemo(
    () => Array.from(new Set(rows.map((r) => r.agent_name))).sort(),
    [rows]
  );
  const classOptions = useMemo(
    () => Array.from(new Set(rows.map((r) => r.class_key))).sort(),
    [rows]
  );

  const filtered = rows.filter((r) => {
    if (agents.length && !agents.includes(r.agent_name)) return false;
    if (classes.length && !classes.includes(r.class_key)) return false;
    return true;
  });

  const dl = (fmt: "csv" | "json") => {
    const url = `${API}/api/o/shadow-mode/export?format=${fmt}&window=${win}`;
    window.location.href = url;
  };

  return (
    <section data-testid="shadow-mode-root">
      <p className="ledger-main__eyebrow">Shadow Mode · unlock evidence</p>
      <h1 className="ledger-main__title">Shadow Mode</h1>
      <div className="sm-controls">
        <div className="sm-controls__group">
          <span className="sm-controls__label">Window</span>
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setWin(w)}
              className={`sm-btn ${w === win ? "sm-btn--active" : ""}`}
              data-testid={`sm-window-${w}`}
            >{w}</button>
          ))}
        </div>
        <div className="sm-controls__group">
          <span className="sm-controls__label">Agent</span>
          <select
            multiple
            value={agents}
            onChange={(e) =>
              setAgents(
                Array.from(e.target.selectedOptions).map((o) => o.value)
              )
            }
            data-testid="sm-agent-filter"
          >
            {agentOptions.map((a) => (
              <option key={a} value={a}>
                {rows.find((r) => r.agent_name === a)?.agent_display || a}
              </option>
            ))}
          </select>
        </div>
        <div className="sm-controls__group">
          <span className="sm-controls__label">Class</span>
          <select
            multiple
            value={classes}
            onChange={(e) =>
              setClasses(
                Array.from(e.target.selectedOptions).map((o) => o.value)
              )
            }
            data-testid="sm-class-filter"
          >
            {classOptions.map((c) => (
              <option key={c} value={c}>
                {rows.find((r) => r.class_key === c)?.class_display || c}
              </option>
            ))}
          </select>
        </div>
        <div className="sm-controls__group sm-controls__group--right">
          <button className="sm-btn" onClick={() => dl("csv")} data-testid="sm-export-csv">
            Export CSV
          </button>
          <button className="sm-btn" onClick={() => dl("json")} data-testid="sm-export-json">
            Export JSON
          </button>
        </div>
      </div>
      {loading ? <p data-testid="sm-loading">Loading…</p> : null}
      {error ? <p className="sm-error" data-testid="sm-error">Error: {error}</p> : null}
      {!loading && !error ? (
        <table className="sm-table" data-testid="sm-table">
          <thead>
            <tr>
              <th>Agent</th><th>Class</th><th>Decisions</th><th>Agreements</th>
              <th>Rate</th><th>E&amp;O misses</th><th>Unlock status</th><th>Last decision</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={`${r.agent_name}:${r.class_key}`}
                  data-testid={`sm-row-${r.agent_name}-${r.class_key}`}>
                <td>{r.agent_display}</td>
                <td>
                  <NavLink to={`/o/shadow-mode/${r.agent_name}/${r.class_key}`}>
                    {r.class_display}
                  </NavLink>
                </td>
                <td className="sm-num">{r.decisions_seen}</td>
                <td className="sm-num">{r.agreements}</td>
                <td>
                  <span className={rateClass(r.agreement_rate)}>
                    {r.agreement_rate === null ? "—"
                      : `${(r.agreement_rate * 100).toFixed(1)}%`}
                  </span>
                </td>
                <td className="sm-num">{r.eno_misses}</td>
                <td><span className={statusClass(r.unlock_status)}>{r.unlock_status}</span></td>
                <td className="sm-ts">{r.last_decision_at ? new Date(r.last_decision_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
            {filtered.length === 0 && !loading ? (
              <tr><td colSpan={8} className="sm-empty">No decisions in window.</td></tr>
            ) : null}
          </tbody>
        </table>
      ) : null}
      <p className="sm-legend">
        Rate colour: <span className="sm-rate sm-rate--verify">≥ 95%</span>{" "}
        <span className="sm-rate sm-rate--copper">80–94.9%</span>{" "}
        <span className="sm-rate sm-rate--alert">&lt; 80%</span>
      </p>
    </section>
  );
}

interface PairRow {
  envelope_id: string;
  ts: string;
  proposal_summary: string;
  human_decision: string;
  agreed: boolean;
  eno_flag: boolean;
  event_refs: {
    agent_output_event_id: string | null;
    approval_event_id: string | null;
  };
}

export function ShadowModeDrill() {
  const { agent = "", class_key = "" } = useParams();
  const [pairs, setPairs] = useState<PairRow[]>([]);
  const [meta, setMeta] = useState<{agent_display?: string; class_display?: string}>({});
  const [modal, setModal] = useState<PairRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/o/shadow-mode/decisions?agent=${agent}&class=${class_key}&window=all`, {
      credentials: "include",
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        setPairs(d.pairs || []);
        setMeta({ agent_display: d.agent_display, class_display: d.class_display });
      })
      .catch((e) => setError(String(e)));
  }, [agent, class_key]);

  return (
    <section data-testid="shadow-mode-drill-root">
      <p className="ledger-main__eyebrow">
        <NavLink to="/o/shadow-mode">Shadow Mode</NavLink> · drill
      </p>
      <h1 className="ledger-main__title">
        {meta.agent_display} · {meta.class_display}
      </h1>
      {error ? <p className="sm-error">Error: {error}</p> : null}
      <table className="sm-table sm-table--drill" data-testid="sm-drill-table">
        <thead>
          <tr>
            <th>Time</th><th>Envelope</th><th>Agent proposal</th>
            <th>Human decision</th><th>Agreed</th><th>E&amp;O</th><th></th>
          </tr>
        </thead>
        <tbody>
          {pairs.map((p) => (
            <tr key={p.event_refs.approval_event_id || p.envelope_id}
                data-testid={`sm-drill-row-${p.envelope_id}`}>
              <td className="sm-ts">{new Date(p.ts).toLocaleString()}</td>
              <td className="sm-mono">{(p.envelope_id || "").slice(0, 8)}…</td>
              <td>{p.proposal_summary}</td>
              <td className={`sm-decision sm-decision--${p.human_decision}`}>
                {p.human_decision}
              </td>
              <td>{p.agreed ? "✓" : "✗"}</td>
              <td>{p.eno_flag ? "!" : ""}</td>
              <td>
                <button className="sm-btn" onClick={() => setModal(p)}
                        data-testid={`sm-drill-open-${p.envelope_id}`}>
                  Open events
                </button>
              </td>
            </tr>
          ))}
          {pairs.length === 0 ? (
            <tr><td colSpan={7} className="sm-empty">No pairs in this window.</td></tr>
          ) : null}
        </tbody>
      </table>
      {modal ? (
        <div className="sm-modal" role="dialog" aria-modal="true"
             onClick={() => setModal(null)} data-testid="sm-modal">
          <div className="sm-modal__body" onClick={(e) => e.stopPropagation()}>
            <div className="sm-modal__head">
              <h2>Event refs</h2>
              <button onClick={() => setModal(null)} className="sm-btn" data-testid="sm-modal-close">Close</button>
            </div>
            <pre className="sm-modal__pre">
{JSON.stringify(modal, null, 2)}
            </pre>
            <p className="sm-modal__hint">
              (Phase 15 will make each event id link into the ledger export view.)
            </p>
          </div>
        </div>
      ) : null}
    </section>
  );
}

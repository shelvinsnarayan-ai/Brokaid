/** R1.0 — Release check panel (Operator): one tap proves the deployed build works. */
import { useCallback, useEffect, useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";
const muted: React.CSSProperties = { fontSize: 12, color: "var(--slate)" };

type Check = { key: string; label: string; status: "pass" | "fail" | "warn" | "info"; detail: string; ms: number };
type Run = { run_id: string; status: string; result: "PASS" | "FAIL" | null; env: string; short_sha?: string;
  started_at: string; finished_at?: string; duration_ms?: number; checks: Check[]; verified: boolean;
  failed_step?: { label: string; detail: string } | null; inject?: string | null };

const fmt = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  const h = d.getHours() % 12 || 12;
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${h}:${p(d.getMinutes())}${d.getHours() < 12 ? "am" : "pm"}`;
};

export function headline(r: Run): string {
  if (r.status === "running") return "Release check running…";
  if (r.result === "FAIL") return `FAILED: ${r.failed_step?.label ?? "a step"} — ${r.failed_step?.detail ?? "see steps below"}`;
  if (r.verified) return `Release ${r.short_sha} verified in production on ${fmt(r.finished_at)}`;
  return `Release ${r.short_sha} passed on ${r.env} (not production) on ${fmt(r.finished_at)}`;
}

const colour = { pass: "#7FD1A8", fail: "#F08A7E", warn: "#F2B01E", info: "var(--slate)" };

function Steps({ checks }: { checks: Check[] }) {
  return (
    <ol style={{ listStyle: "none", padding: 0, margin: "8px 0", display: "grid", gap: 4 }} data-testid="rc-steps">
      {checks.map((c) => (
        <li key={c.key} data-testid={`rc-step-${c.key}`} style={{ fontSize: 13, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span style={{ color: colour[c.status], fontWeight: 700, minWidth: 40 }}>{c.status.toUpperCase()}</span>
          <span style={{ minWidth: 0, flex: "1 1 220px" }}>{c.label}: <span style={muted}>{c.detail}</span></span>
          <span style={muted}>{c.ms >= 1000 ? `${(c.ms / 1000).toFixed(1)}s` : `${c.ms}ms`}</span>
        </li>
      ))}
    </ol>
  );
}

export function ReleaseCheck() {
  const [latest, setLatest] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => {
    const r = await fetch(`${API}/api/o/release-check/latest`, { credentials: "include" });
    if (!r.ok) return;
    const b = await r.json();
    setLatest(b.latest); setHistory(b.history);
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (latest?.status !== "running") return;
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [latest?.status, load]);
  const start = async () => {
    setErr(null);
    const r = await fetch(`${API}/api/o/release-check`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (r.status === 409) setErr("A release check is already running. Wait for it to finish.");
    else if (!r.ok) setErr("Couldn't start the release check.");
    await load();
  };
  const running = latest?.status === "running";
  return (
    <section data-testid="release-check" style={{ margin: "0 0 24px" }}>
      <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>Release check</h2>
      <p style={{ ...muted, margin: "0 0 10px" }}>
        Runs a synthetic change for "Release Check Pty Ltd" through the live app (about a minute). Test data only; no emails or pushes.
      </p>
      <button className="k-btn" onClick={() => void start()} disabled={running} data-testid="rc-start">
        {running ? "Running…" : "Run release check"}</button>
      {err && <p style={{ ...muted, color: "#F08A7E" }} data-testid="rc-error">{err}</p>}
      {latest && (
        <div style={{ marginTop: 12 }}>
          <p data-testid="rc-headline" style={{ fontSize: 20, fontWeight: 700, margin: "0 0 4px", overflowWrap: "anywhere",
            color: latest.result === "FAIL" ? "#F08A7E" : latest.result === "PASS" ? "#7FD1A8" : "inherit" }}>{headline(latest)}</p>
          <span style={muted} data-testid="rc-run-id">Run {latest.run_id.slice(0, 8)}{latest.duration_ms ? ` · ${(latest.duration_ms / 1000).toFixed(1)}s` : ""}
            {latest.inject ? " · forced failure test" : ""}</span>
          <Steps checks={latest.checks} />
        </div>
      )}
      {history.length > 1 && (
        <details data-testid="rc-history">
          <summary style={{ cursor: "pointer", fontSize: 14 }}>Last {history.length} runs</summary>
          <ul style={{ listStyle: "none", padding: 0, margin: "6px 0", display: "grid", gap: 2 }}>
            {history.map((h) => (
              <li key={h.run_id} style={{ fontSize: 13, overflowWrap: "anywhere" }} data-testid={`rc-history-${h.run_id.slice(0, 8)}`}>
                {fmt(h.started_at)} · {h.short_sha ?? "—"} · {h.env} · <b>{h.status === "running" ? "RUNNING" : h.result}</b>
                {h.verified ? " · verified" : ""}{h.inject ? " · forced" : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

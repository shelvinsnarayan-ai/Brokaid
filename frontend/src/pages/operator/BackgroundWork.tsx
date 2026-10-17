/** R1.1 — Background work panel (Operator): queue health, scheduler leader, dead-letter retry/discard. */
import { useCallback, useEffect, useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";
const muted: React.CSSProperties = { fontSize: 12, color: "var(--slate)" };

type Dead = { job_id: string; label: string; attempts: number; last_error: string | null; finished_at: string | null; envelope_id?: string };
type Summary = { counts: Record<string, number>; oldest_queued_s: number; leader: { owner: string | null; alive: boolean }; dead: Dead[] };

const age = (s: number) => (s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

export function BackgroundWork() {
  const [s, setS] = useState<Summary | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => {
    const r = await fetch(`${API}/api/o/jobs/summary`, { credentials: "include" });
    if (r.ok) setS(await r.json());
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 10000);
    return () => clearInterval(t);
  }, [load]);
  const act = async (id: string, what: "retry" | "discard") => {
    const r = await fetch(`${API}/api/o/jobs/${id}/${what}`, { method: "POST", credentials: "include" });
    setNote(r.ok ? (what === "retry" ? "Queued again." : "Discarded.") : "That job is no longer in the failed list.");
    await load();
  };
  if (!s) return null;
  const c = s.counts;
  return (
    <section data-testid="background-work" style={{ margin: "0 0 24px" }}>
      <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>Background work</h2>
      <p style={{ fontSize: 14, margin: "0 0 6px" }} data-testid="bw-counts">
        {c.queued} queued · {c.running} running · <span style={{ color: c.dead ? "#F08A7E" : "inherit" }}>{c.dead} failed for good</span>
        {s.oldest_queued_s > 0 && <> · oldest waiting {age(s.oldest_queued_s)}</>}
      </p>
      <p style={muted} data-testid="bw-leader">
        Scheduler: {s.leader.alive ? "running" : "NOT RUNNING — reminders, emails and pushes are paused"}
      </p>
      {s.dead.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: "8px 0", display: "grid", gap: 8 }} data-testid="bw-dead-list">
          {s.dead.map((d) => (
            <li key={d.job_id} data-testid={`bw-dead-${d.job_id.slice(0, 8)}`} style={{ fontSize: 13, overflowWrap: "anywhere" }}>
              <b>{d.label}</b> · tried {d.attempts}× · <span style={muted}>{d.last_error}</span>
              <span style={{ display: "inline-flex", gap: 6, marginLeft: 8 }}>
                <button className="k-btn" onClick={() => void act(d.job_id, "retry")} data-testid={`bw-retry-${d.job_id.slice(0, 8)}`}>Retry</button>
                <button className="k-btn" onClick={() => void act(d.job_id, "discard")} data-testid={`bw-discard-${d.job_id.slice(0, 8)}`}>Discard</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {note && <p style={muted} data-testid="bw-note">{note}</p>}
    </section>
  );
}

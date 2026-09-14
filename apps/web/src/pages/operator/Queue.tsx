/**
 * /o/queue — full pending list with keyboard nav (j/k/Enter/a/r).
 * SLA countdown recomputed every 5s.
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { KChip } from "../../components/ledger/KChip";
import { KEmpty } from "../../components/ledger/KEmpty";
import { KKbd } from "../../components/ledger/KKbd";
import { KNumber } from "../../components/ledger/KNumber";
import { useOperatorStore } from "../../state/operatorStore";

const API = import.meta.env.VITE_API_BASE || "";

interface Card {
  envelope_id: string;
  class_key: string;
  matter_id: string | null;
  client_id: string | null;
  hitl_since: string;
  sla_deadline: string | null;
  sla_state: "ok" | "warn" | "overdue";
  gate_reason: string | null;
  confidence: number | null;
  routed_to_role: string | null;
  client_display?: string | null;
  matter_display?: string | null;
}

function countdown(deadline: string | null, now: Date): string {
  if (!deadline) return "—";
  const t = new Date(deadline).getTime() - now.getTime();
  const abs = Math.abs(t);
  const h = Math.floor(abs / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const s = Math.floor((abs % 60_000) / 1000);
  const sign = t < 0 ? "-" : "";
  if (h > 0) return `${sign}${h}h ${m}m`;
  if (m > 0) return `${sign}${m}m ${s}s`;
  return `${sign}${s}s`;
}

export function Queue() {
  const nav = useNavigate();
  const toast = useOperatorStore((s) => s.toast);
  const [cards, setCards] = useState<Card[]>([]);
  const [selected, setSelected] = useState(0);
  const [filter, setFilter] = useState("");
  const [now, setNow] = useState(new Date());
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/o/queue`, { credentials: "include" });
      if (!r.ok) { setError(`${r.status}`); return; }
      const b = await r.json();
      setCards(b.cards);
      setError(null);
    } catch (e) { setError(String(e)); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 5000);
    return () => clearInterval(t);
  }, []);

  const visible = filter
    ? cards.filter((c) =>
        (c.class_key + " " + (c.client_display ?? "") + " " + (c.gate_reason ?? ""))
          .toLowerCase().includes(filter.toLowerCase()))
    : cards;

  const act = useCallback(async (env_id: string, path: "approve" | "reject", body?: any) => {
    const r = await fetch(`${API}/api/o/queue/${env_id}/${path}`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const bo = await r.json().catch(() => ({}));
    if (!r.ok) { toast(`${path} ${r.status}: ${bo.detail || ""}`, "alert"); return; }
    toast(`${path} · ${bo.state}`, "verify");
    void load();
  }, [load, toast]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") {
        if (e.key === "Escape") (target as HTMLElement).blur();
        return;
      }
      if (e.key === "j") { e.preventDefault(); setSelected((s) => Math.min(s + 1, Math.max(visible.length - 1, 0))); }
      else if (e.key === "k") { e.preventDefault(); setSelected((s) => Math.max(s - 1, 0)); }
      else if (e.key === "Enter" && visible[selected]) {
        e.preventDefault(); nav(`/o/queue/${visible[selected].envelope_id}`);
      }
      else if (e.key === "a" && visible[selected]) {
        e.preventDefault();
        if (window.confirm(`Approve ${visible[selected].class_key}?`)) {
          void act(visible[selected].envelope_id, "approve");
        }
      }
      else if (e.key === "r" && visible[selected]) {
        e.preventDefault();
        const reason = window.prompt(`Reject ${visible[selected].class_key}? Reason:`);
        if (reason !== null) void act(visible[selected].envelope_id, "reject", { reason });
      }
      else if (e.key === "/") {
        e.preventDefault();
        document.getElementById("queue-filter")?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [visible, selected, nav, act]);

  return (
    <section data-testid="queue-page">
      <p className="ledger-main__eyebrow">Queue</p>
      <h1 className="ledger-main__title">Pending decisions</h1>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
        <input
          id="queue-filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter (/)"
          className="ledger-topbar__cmd"
          style={{ width: 240 }}
          data-testid="queue-filter"
        />
        <span style={{ color: "var(--bone-muted)", fontSize: 12 }}>
          <KKbd>j</KKbd>/<KKbd>k</KKbd> move · <KKbd>Enter</KKbd> open · <KKbd>a</KKbd> approve · <KKbd>r</KKbd> reject
        </span>
      </div>

      {error && <div className="k-empty" data-testid="queue-error">Error: {error}</div>}

      {visible.length === 0 ? (
        <KEmpty title="Queue clear" hint="No pending decisions." testId="queue-empty" />
      ) : (
        <table className="k-table" data-testid="queue-table">
          <thead>
            <tr>
              <th>SLA</th><th>Class</th><th>Client</th><th>Reason</th>
              <th className="k-num-th">Confidence</th><th className="k-num-th">Countdown</th>
              <th>Route</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((c, i) => (
              <tr
                key={c.envelope_id}
                className={[
                  i === selected ? "is-selected" : "",
                  c.sla_state === "overdue" ? "is-overdue" : "",
                  c.sla_state === "warn" ? "is-warn" : "",
                ].filter(Boolean).join(" ")}
                onClick={() => setSelected(i)}
                onDoubleClick={() => nav(`/o/queue/${c.envelope_id}`)}
                data-testid={`queue-row-${i}`}
              >
                <td><KChip variant={c.sla_state}>{c.sla_state}</KChip></td>
                <td>{c.class_key}</td>
                <td>{c.client_display ?? "—"}</td>
                <td style={{ color: "var(--bone-muted)", fontSize: 12 }}>{c.gate_reason ?? "—"}</td>
                <td className="k-num"><KNumber value={c.confidence} format="raw" /></td>
                <td className="k-num" data-testid={`queue-countdown-${i}`}>{countdown(c.sla_deadline, now)}</td>
                <td>{c.routed_to_role ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

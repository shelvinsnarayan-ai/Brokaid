/**
 * /o/queue/:envelope_id — three-column Decision Card.
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { KChip } from "../../components/ledger/KChip";
import { KConfidenceBar } from "../../components/ledger/KConfidenceBar";
import { KEvidence } from "../../components/ledger/KEvidence";
import { KKbd } from "../../components/ledger/KKbd";
import { KNumber } from "../../components/ledger/KNumber";
import { useOperatorStore } from "../../state/operatorStore";

const API = import.meta.env.VITE_API_BASE || "";

interface CardResp {
  card: {
    envelope_id: string; class_key: string;
    sla_state: "ok" | "warn" | "overdue";
    sla_deadline: string | null; hitl_since: string;
    gate_reason: string | null; confidence: number | null;
    client_display?: string | null; matter_display?: string | null;
    routed_to_role: string | null;
  };
  proposal: { event_id: string; ts: string; type: string; payload: any };
  evidence: { event_id: string; ts: string; type: string; payload: any }[];
  confidence: {
    components: { schema_validity: number; completeness: number;
                  citation_existence: number; class_agreement: number };
    scalar: number | null; source: string;
  };
  mocked_capabilities: string[];
}

function countdown(deadline: string | null): string {
  if (!deadline) return "—";
  const t = new Date(deadline).getTime() - Date.now();
  const abs = Math.abs(t);
  const h = Math.floor(abs / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  return `${t < 0 ? "-" : ""}${h}h ${m}m`;
}

export function DecisionCard() {
  const { envelope_id = "" } = useParams();
  const nav = useNavigate();
  const [data, setData] = useState<CardResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [_, setTick] = useState(0);
  const toast = useOperatorStore((s) => s.toast);
  const session = useOperatorStore((s) => s.session);

  useEffect(() => {
    (async () => {
      const r = await fetch(`${API}/api/o/queue/${envelope_id}`, { credentials: "include" });
      if (!r.ok) { setError(`${r.status}`); return; }
      setData(await r.json());
    })();
    const t = setInterval(() => setTick((n) => n + 1), 5000);
    return () => clearInterval(t);
  }, [envelope_id]);

  async function action(path: "approve" | "edit" | "reject" | "block", body?: any) {
    const r = await fetch(`${API}/api/o/queue/${envelope_id}/${path}`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const bo = await r.json().catch(() => ({}));
    if (!r.ok) { toast(`${path} ${r.status}: ${bo.detail || ""}`, "alert"); return; }
    toast(`${path} · ${bo.state}`, "verify");
    setTimeout(() => nav("/o/queue"), 600);
  }

  if (error) return <div className="k-empty" data-testid="decision-card-error">Error: {error}</div>;
  if (!data) return <div className="k-empty" data-testid="decision-card-loading">Loading decision card…</div>;

  const { card, proposal, evidence, confidence, mocked_capabilities } = data;
  const isTrust = card.class_key === "trust_money";
  const isCompliance = session?.session.active_role === "compliance";

  return (
    <section data-testid="decision-card-page">
      <p className="ledger-main__eyebrow">Queue · {card.class_key}</p>
      <h1 className="ledger-main__title">Decision card</h1>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <KChip variant={card.sla_state} testId="dcard-sla-chip">{card.sla_state} · {countdown(card.sla_deadline)}</KChip>
        <KChip variant="info" testId="dcard-class-chip">{card.class_key}</KChip>
        {card.routed_to_role && <KChip variant="role">Routed · {card.routed_to_role}</KChip>}
        {mocked_capabilities.map((cap) => (
          <MockedBadge key={cap} label={cap as any} variant="operator" />
        ))}
        {isTrust && <KChip variant="warn" testId="dcard-two-person">two-person rule</KChip>}
      </div>

      <div className="dcard-grid">
        <div className="dcard-col" data-testid="dcard-proposal">
          <h3>Proposal</h3>
          <div style={{ marginBottom: 8, color: "var(--slate)", fontSize: 11 }}>
            {proposal.type} · {proposal.event_id.slice(0, 8)}… · {proposal.ts.replace("T", " ").slice(0, 19)}
          </div>
          <p style={{ marginTop: 0 }}>
            {proposal.payload?.output?.summary || <em style={{ color: "var(--slate)" }}>(no summary field)</em>}
          </p>
          {card.client_display && (
            <p style={{ color: "var(--slate)", fontSize: 12 }}>
              Client · {card.client_display}
            </p>
          )}
          <div className="dcard-actions">
            <button className="k-btn k-btn--primary" onClick={() => action("approve")} data-testid="dcard-approve">
              <KKbd>a</KKbd> Approve
            </button>
            <button className="k-btn" onClick={() => {
              const raw = window.prompt("Edits (JSON, e.g. {\"limit\": 500000}):", "{}");
              if (raw !== null) { try { void action("edit", { edits: JSON.parse(raw) }); } catch { toast("bad JSON", "alert"); } }
            }} data-testid="dcard-edit">
              <KKbd>e</KKbd> Edit
            </button>
            <button className="k-btn k-btn--danger" onClick={() => {
              const reason = window.prompt("Reject reason:");
              if (reason !== null) void action("reject", { reason });
            }} data-testid="dcard-reject">
              <KKbd>r</KKbd> Reject
            </button>
            <button
              className="k-btn"
              disabled={!isCompliance}
              title={isCompliance ? "" : "Compliance role only"}
              onClick={() => {
                const reason = window.prompt("Block reason:");
                if (reason !== null) void action("block", { reason });
              }}
              data-testid="dcard-block"
            >
              <KKbd>b</KKbd> Block
            </button>
          </div>
        </div>

        <div className="dcard-col" data-testid="dcard-evidence">
          <h3>Evidence</h3>
          <KEvidence items={evidence} />
        </div>

        <div className="dcard-col" data-testid="dcard-confidence">
          <h3>Confidence</h3>
          <KConfidenceBar
            components={confidence.components}
            scalar={confidence.scalar}
            source={confidence.source}
          />
          <div style={{ marginTop: 12, padding: 10, background: "rgba(232,226,214,0.03)", borderRadius: 3 }}>
            <div style={{ fontSize: 11, color: "var(--slate)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
              Gate reason
            </div>
            <div style={{ fontSize: 12 }}>{card.gate_reason || "—"}</div>
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--slate)" }}>
              HITL since {card.hitl_since.replace("T", " ").slice(0, 19)} · confidence {" "}
              <KNumber value={card.confidence} format="raw" />
            </div>
          </div>
          {isTrust && (
            <div style={{ marginTop: 12, padding: 10, border: "1px solid var(--copper)", borderRadius: 3, fontSize: 12 }} data-testid="dcard-two-person-note">
              <strong>Two-person rule.</strong> This trust_money card needs approvals from two
              distinct accounts users. First approval will hold in "pending".
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

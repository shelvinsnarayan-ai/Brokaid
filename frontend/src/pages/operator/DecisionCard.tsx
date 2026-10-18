/**
 * /o/queue/:envelope_id — three-column Decision Card.
 */
import { OfflineSign } from "./OfflineSign";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { KChip } from "../../components/ledger/KChip";
import { KConfidenceBar } from "../../components/ledger/KConfidenceBar";
import { KEvidence } from "../../components/ledger/KEvidence";
import { KKbd } from "../../components/ledger/KKbd";
import { KNumber } from "../../components/ledger/KNumber";
import { useOperatorStore } from "../../state/operatorStore";
import { labelFor } from "../../lib/labels";
import { EnterInsurerQuote } from "./EnterInsurerQuote";
import { fmtDateTime, fmtSydDate, fmtSydTime } from "../../lib/format";

const confidenceWord = (s: number | null | undefined) =>
  s == null ? "Not scored" : s >= 0.85 ? "High" : s >= 0.6 ? "Medium" : "Low";
const confidenceReason = (components: any, noEvidence: boolean) => {
  if (noEvidence) return "nothing is cited, so a broker has to check it from scratch";
  const entries = Object.entries((components || {}) as Record<string, number>).filter(([, v]) => typeof v === "number");
  if (!entries.length) return "based on the cited evidence";
  const [weak] = entries.sort((a, b) => a[1] - b[1])[0];
  return `weakest signal is ${weak.replace(/_/g, " ")}`;
};

const resolvedVerb = (r: { event_type: string | null; verdict: string | null }) => {
  const v = (r.verdict || r.event_type || "").toLowerCase();
  if (v.includes("reject")) return "Rejected";
  if (v.includes("block")) return "Blocked";
  if (v.includes("edit")) return "Edited";
  if (v.includes("stale")) return "Closed as stale";
  if (v.includes("abandon")) return "Abandoned";
  return "Approved";
};
import "../../styles/phase13.css";

const API = import.meta.env.VITE_API_BASE || "";

interface CardResp {
  resolution?: { event_type: string | null; verdict: string | null; actor_name: string | null; resolved_at: string } | null;
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
  return t < 0 ? `overdue by ${h}h ${m}m` : `${h}h ${m}m`;
}

export function DecisionCard() {
  const { envelope_id = "" } = useParams();
  const nav = useNavigate();
  const [data, setData] = useState<CardResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [_, setTick] = useState(0);
  const [showWhy, setShowWhy] = useState(false);
  const [packetReady, setPacketReady] = useState(false);
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

  function approveWithConfirm() {
    if (window.confirm(`Approve ${data ? labelFor(data.card.class_key) : "this proposal"}?`)) void action("approve");
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || e.metaKey || e.ctrlKey) return;
      if (e.key === "a" && data && !data.resolution && data.evidence.length > 0) { e.preventDefault(); approveWithConfirm(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  useEffect(() => {
    if (!envelope_id) return;
    fetch(`${API}/api/o/envelope/${envelope_id}/change-packet?probe=1`, { credentials: "include" })
      .then((r) => setPacketReady(r.ok))
      .catch(() => setPacketReady(false));
  }, [envelope_id]);

  if (error) return <div className="k-empty" data-testid="decision-card-error">Error: {error}</div>;
  if (!data) return <div className="k-empty" data-testid="decision-card-loading">Loading decision card…</div>;

  const { card, proposal, evidence, confidence, mocked_capabilities } = data;
  const compValues = Object.values(confidence.components || {}).map(Number);
  const staleFormat = compValues.length > 0 && compValues.every((v) => v === 0)
    && confidence.scalar !== null && Math.abs(Number(confidence.scalar) - 0.9) < 1e-9;
  const cites: unknown[] = (proposal as any)?.payload?.citations || [];
  const mismatch = proposal?.type === "RequestNeedsHumanReview" ? (proposal as any).payload?.mismatch : null;
  const objectCites = evidence.length === 0 && cites.some((c) => c !== null && typeof c === "object");
  const isTrust = card.class_key === "trust_money";
  const isClaim = card.class_key === "claim_admin";
  const claimId = isClaim ? (proposal.payload?.claim_id as string | undefined) : undefined;
  const isCompliance = session?.session.active_role === "compliance";
  // R0.6: engine detail (scores) is Operator-only (role=operator).
  const isOperator = session?.session.active_role === "operator";
  const noEvidence = evidence.length === 0;

  return (
    <section data-testid="decision-card-page">
      <p className="ledger-main__eyebrow">Queue · {labelFor(card.class_key)}</p>
      <h1 className="ledger-main__title">Decision card</h1>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <KChip variant={card.sla_state} testId="dcard-sla-chip">{card.sla_state} · {countdown(card.sla_deadline)}</KChip>
        <KChip variant="info" testId="dcard-class-chip">{labelFor(card.class_key)}</KChip>
        {card.routed_to_role && <KChip variant="role">Routed · {labelFor(card.routed_to_role)}</KChip>}
        {mocked_capabilities.map((cap) => (
          <MockedBadge key={cap} label={cap as any} variant="operator" />
        ))}
        {isTrust && <KChip variant="warn" testId="dcard-two-person">two-person rule</KChip>}
      </div>

      {isClaim && claimId ? (
        <div className="dcard-claim-cta" data-testid="dcard-open-claim-composer-wrap">
          <a
            href={`/o/claims/${claimId}`}
            className="k-btn k-btn--primary"
            data-testid="dcard-open-claim-composer"
          >
            Open claim composer →
          </a>
          <span className="dcard-claim-cta__hint">
            Full FNOL text, MOCKED · INSURER timeline, and the
            coverage-language-guarded status update composer.
          </span>
        </div>
      ) : null}

      <div className="dcard-grid">
        <div className="dcard-col" data-testid="dcard-proposal">
          <h3>Proposal</h3>
          <div style={{ marginBottom: 8, color: "var(--slate)", fontSize: 11 }}>
            {labelFor(proposal.type)}{isOperator ? ` · ${proposal.event_id.slice(0, 8)}…` : ""} · {fmtDateTime(proposal.ts)}
          </div>
          {mismatch ? (
            <div data-testid="dcard-mismatch" style={{ fontSize: 12 }}>
              <p style={{ marginTop: 0, color: "var(--alert)" }}>
                {(proposal as any).payload?.stage === "advice_boundary"
                  ? "Parked: this needs personal advice from a broker. Nothing was quoted."
                  : (proposal as any).payload?.stage === "enter_insurer_quote"
                  ? "Parked: get a real quote from the insurer and enter it below. Nothing was auto-priced."
                  : "Parked: the request doesn't match what the policy holds. Nothing was quoted."}
              </p>
              {(proposal as any).payload?.stage !== "advice_boundary" && (
                <>
                  <p>Field · <code data-testid="dcard-mismatch-field">{mismatch.field_path}</code></p>
                  <p>Client said · <code data-testid="dcard-mismatch-value">{typeof mismatch.attempted_value === "object" ? Object.values(mismatch.attempted_value ?? {}).join(" ") : String(mismatch.attempted_value)}</code></p>
                </>
              )}
              <p>Check · <span data-testid="dcard-mismatch-reason">{mismatch.reason}</span></p>
              {(proposal as any).payload?.stage === "enter_insurer_quote" && !data.resolution
                ? <EnterInsurerQuote envelopeId={envelope_id!} onDone={() => setTick((n) => n + 1)} /> : null}
            </div>
          ) : (
            <p style={{ marginTop: 0 }}>
              {proposal.payload?.output?.summary
                ? String(proposal.payload.output.summary).replace(/\b[a-z]+(?:_[a-z0-9]+)+\b/g, (k) => labelFor(k).toLowerCase()).replace("awaiting operator review", "waiting for a broker")
                : <em style={{ color: "var(--slate)" }}>No summary given</em>}
            </p>
          )}
          {card.client_display && (
            <p style={{ color: "var(--slate)", fontSize: 12 }}>
              Client · {card.client_display}
            </p>
          )}
          {data.resolution ? (
            <p className="dcard-resolved" data-testid="dcard-resolved-badge"
              style={{ fontSize: 12, color: "var(--slate)", border: "1px solid var(--line, #1E2E5A)", padding: "6px 10px", display: "inline-block" }}>
              Resolved · {resolvedVerb(data.resolution)} by {data.resolution.actor_name || "broker"} on {fmtSydDate(data.resolution.resolved_at)} at {fmtSydTime(data.resolution.resolved_at)} AEST
            </p>
          ) : (
          <div className="dcard-actions">
            {noEvidence ? (
              <p style={{ fontSize: 12, color: "var(--alert)", margin: "0 0 8px" }} data-testid="dcard-no-evidence">
                No cited evidence — this can't be approved from here. Edit it, decline it, or send it to the principal.
              </p>
            ) : (
            <button className="k-btn k-btn--primary" onClick={approveWithConfirm} data-testid="dcard-approve">
              <KKbd>a</KKbd> Approve
            </button>
            )}
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
          )}
        </div>

        <div className="dcard-col" data-testid="dcard-evidence">
          <h3>Evidence</h3>
          <div className="dcard-packet" style={{ marginBottom: 12, fontSize: 12 }}>
            {packetReady ? (
              <a href={`${API}/api/o/envelope/${card.envelope_id}/change-packet`} target="_blank" rel="noreferrer"
                className="k-btn" data-testid="dcard-change-packet">
                Change packet
              </a>
            ) : (
              <button type="button" className="k-btn" disabled aria-disabled="true"
                title="Available once the client has signed" data-testid="dcard-change-packet-disabled">
                Change packet
              </button>
            )}
            <span style={{ marginLeft: 8, color: "var(--slate)" }} data-testid="dcard-change-packet-note">
              {packetReady ? "Not sent to iAdviser. Key manually." : "Not sent to iAdviser. Key manually. Available once the client has signed."}
            </span>
          </div>
          <KEvidence items={evidence} />
          {objectCites && (
            <div style={{ marginTop: 8, fontSize: 12 }}>
              <button type="button" className="k-btn" onClick={() => setShowWhy((v) => !v)} data-testid="dcard-evidence-why">
                Why?
              </button>
              {showWhy && (
                <p style={{ color: "var(--slate)", marginTop: 6 }} data-testid="dcard-evidence-why-text">
                  This proposal cites schedule fields and documents rather than ledger events, so the
                  references can't be shown as evidence rows yet. Check the proposal details directly.
                </p>
              )}
            </div>
          )}
        </div>

        <div className="dcard-col" data-testid="dcard-confidence">
          <h3>Confidence</h3>
          {!isOperator && !mismatch ? (
            <p style={{ fontSize: 13 }} data-testid="dcard-confidence-word">
              <strong>{confidenceWord(confidence.scalar)}</strong> — {confidenceReason(confidence.components, noEvidence)}
            </p>
          ) : mismatch ? (
            <p style={{ fontSize: 12, color: "var(--slate)" }} data-testid="dcard-not-scored">
              Not scored — parked by the policy-field check before any quote was drafted.
            </p>
          ) : staleFormat ? (
            <div data-testid="dcard-evidence-pending">
              <p style={{ fontSize: 12, color: "var(--alert)", marginBottom: 8 }}>
                Evidence pending — this proposal was generated before the current evidence-capture rules; approve with care.
              </p>
              <p style={{ fontSize: 12, color: "var(--slate)", fontWeight: 400 }} data-testid="dcard-scalar-subdued">
                Overall confidence {Number(confidence.scalar).toFixed(2)}
              </p>
            </div>
          ) : (
            <KConfidenceBar
              components={confidence.components}
              scalar={confidence.scalar}
              source={confidence.source}
            />
          )}
          <div style={{ marginTop: 12, padding: 10, background: "rgba(230,234,242,0.03)", borderRadius: 3 }}>
            <div style={{ fontSize: 11, color: "var(--slate)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
              Gate reason
            </div>
            <div style={{ fontSize: 12 }} data-testid="dcard-gate-reason">{labelFor(card.gate_reason)}</div>
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--slate)" }} data-testid="dcard-waiting-since">
              Waiting since {fmtDateTime(card.hitl_since)}
              {isOperator && (<> · engine detail <KNumber value={card.confidence} format="raw" /></>)}
            </div>
          </div>
          {isTrust && (
            <div style={{ marginTop: 12, padding: 10, border: "1px solid var(--copper)", borderRadius: 3, fontSize: 12 }} data-testid="dcard-two-person-note">
              <strong>Two-person rule.</strong> This trust-money card needs approvals from two
              distinct accounts users. First approval will hold in "pending".
            </div>
          )}
          <OfflineSign kind="endorsement" refId={envelope_id} />
        </div>
      </div>
    </section>
  );
}

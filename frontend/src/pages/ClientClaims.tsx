/**
 * Phase 13 — client claim scene.
 *
 * - ClientClaims: list of claims for the linked client.
 * - ClaimsNew:    FNOL form (POST /api/c/claims).
 * - ClaimDetail:  claim header + timeline (GET /api/c/claims/:id).
 */
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import "../styles/phase13.css";
import { fmtDateTime } from "../lib/format";

const API = (import.meta as any).env.VITE_API_BASE || "";

/**
 * Defensive date formatter for claim surfaces.
 *
 * The seed occasionally lands with a null `opened_at` (and `resolved_at`
 * is nullable by design). `new Date(null).toLocaleString()` renders as
 * "1/1/1970, 12:00:00 AM" which is user-hostile on demo surfaces. Fall
 * back to the em-dash used across the rest of the codebase for
 * unavailable values (`c.client_display ?? "—"` etc.) so the card
 * degrades honestly.
 */
function formatClaimTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return fmtDateTime(d);
}

interface ClaimListItem {
  claim_id: string;
  claim_reference: string;
  claim_type: string | null;
  urgency: string | null;
  status: string;
  opened_at: string;
  description_preview: string;
}

interface TimelineRow {
  seq: number;
  ts: string;
  kind: string;
  summary: string;
  mocked: boolean;
}

interface ClaimDetailShape {
  claim: {
    claim_id: string;
    claim_reference: string;
    claim_type: string | null;
    urgency: string | null;
    status: string;
    opened_at: string;
    resolved_at: string | null;
    description: string;
  };
  timeline: TimelineRow[];
}


function UrgencyPill({ urgency }: { urgency: string | null }) {
  if (!urgency) return null;
  return (
    <span
      className={`brokaid-urgency brokaid-urgency--${urgency}`}
      data-testid={`urgency-${urgency}`}
    >
      {urgency}
    </span>
  );
}

export function ClientClaims() {
  const [claims, setClaims] = useState<ClaimListItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetch(`${API}/api/c/claims`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => setClaims(d.claims || []))
      .catch((e) => setErr(String(e)));
  }, []);
  return (
    <section className="brokaid-tab" data-testid="tab-claims-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Claims</p>
        <h1 className="brokaid-tab__title">Claims</h1>
        <Link
          to="/c/claims/new"
          className="brokaid-btn brokaid-btn--primary"
          data-testid="start-a-claim"
        >
          Start a claim
        </Link>
      </header>

      {err ? <p className="brokaid-error" data-testid="claims-error">Couldn't load claims.</p> : null}

      {claims && claims.length === 0 ? (
        <EmptyState
          title="No open claims"
          body="Report an incident here. Your broker sees it the same second."
          testId="claims-empty"
        />
      ) : null}

      {claims && claims.length > 0 ? (
        <ul className="brokaid-claims-list" data-testid="claims-list">
          {claims.map((c) => (
            <li key={c.claim_id}>
              <Link
                to={`/c/claims/${c.claim_id}`}
                className="brokaid-claim-card"
                data-testid={`claim-card-${c.claim_reference}`}
              >
                <div className="brokaid-claim-card__row-top">
                  <span className="brokaid-claim-card__ref">
                    {c.claim_reference}
                  </span>
                  <UrgencyPill urgency={c.urgency} />
                </div>
                <div className="brokaid-claim-card__row-mid">
                  <span className="brokaid-claim-card__type">
                    {(c.claim_type || "general").replace("_", " ")}
                  </span>
                  <span
                    className={`brokaid-claim-card__status brokaid-claim-card__status--${c.status}`}
                    data-testid={`claim-status-${c.status}`}
                  >
                    {c.status}
                  </span>
                </div>
                <p className="brokaid-claim-card__preview">
                  {c.description_preview}
                </p>
                <p className="brokaid-claim-card__ts">
                  Opened {formatClaimTs(c.opened_at)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function ClaimsNew() {
  const nav = useNavigate();
  const [description, setDescription] = useState("");
  const [incidentDate, setIncidentDate] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setSubmitting(true);
    try {
      const r = await fetch(`${API}/api/c/claims`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description,
          incident_date: incidentDate || null,
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const j = await r.json();
      nav(`/c/claims/${j.claim_id}`, { replace: true });
    } catch (e: any) {
      setErr(e.message || "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="brokaid-tab" data-testid="claims-new-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Claims / New</p>
        <h1 className="brokaid-tab__title">Report an incident</h1>
      </header>

      <form
        className="brokaid-fnol"
        onSubmit={onSubmit}
        data-testid="fnol-form"
      >
        <label className="brokaid-field">
          <span>Incident date</span>
          <input
            type="date"
            value={incidentDate}
            onChange={(e) => setIncidentDate(e.target.value)}
            data-testid="fnol-incident-date"
          />
        </label>

        <label className="brokaid-field">
          <span>What happened?</span>
          <textarea
            required
            minLength={8}
            rows={8}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="A short account of the incident. Include time, place, and any injuries."
            data-testid="fnol-description"
          />
        </label>

        {err ? (
          <p className="brokaid-error" data-testid="fnol-error">
            {err}
          </p>
        ) : null}

        <div className="brokaid-fnol__actions">
          <Link
            to="/c/claims"
            className="brokaid-btn brokaid-btn--secondary"
            data-testid="fnol-cancel"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={submitting}
            className="brokaid-btn brokaid-btn--primary"
            data-testid="fnol-submit"
          >
            {submitting ? "Submitting…" : "Submit claim"}
          </button>
        </div>

        <p className="brokaid-fnol__mocked-note">
          Your broker lodges this with the insurer and keeps you posted.
        </p>
      </form>
    </section>
  );
}

export function ClaimDetail() {
  const { claim_id } = useParams<{ claim_id: string }>();
  const [detail, setDetail] = useState<ClaimDetailShape | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!claim_id) return;
    fetch(`${API}/api/c/claims/${claim_id}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => setDetail(d))
      .catch((e) => setErr(String(e)));
  }, [claim_id]);

  if (err)
    return (
      <section className="brokaid-tab">
        <p className="brokaid-error" data-testid="claim-detail-error">
          Couldn't load claim.
        </p>
      </section>
    );
  if (!detail)
    return (
      <section className="brokaid-tab">
        <p data-testid="claim-detail-loading">Loading…</p>
      </section>
    );

  const c = detail.claim;
  return (
    <section className="brokaid-tab" data-testid="claim-detail-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Claim</p>
        <h1 className="brokaid-tab__title" data-testid="claim-detail-ref">
          {c.claim_reference}
        </h1>
        <div className="brokaid-claim-detail__pills">
          <span
            className={`brokaid-claim-card__status brokaid-claim-card__status--${c.status}`}
            data-testid={`claim-detail-status-${c.status}`}
          >
            {c.status}
          </span>
          <UrgencyPill urgency={c.urgency} />
          <span className="brokaid-claim-card__type">
            {(c.claim_type || "general").replace("_", " ")}
          </span>
        </div>
      </header>

      <div className="brokaid-claim-detail__body">
        <p className="brokaid-claim-detail__opened">
          Opened {formatClaimTs(c.opened_at)}
        </p>
        <blockquote className="brokaid-claim-detail__desc">
          {c.description}
        </blockquote>
      </div>

      <h2 className="brokaid-claim-timeline__title">Timeline</h2>
      <ol
        className="brokaid-claim-timeline"
        data-testid="claim-timeline"
      >
        {detail.timeline.map((t) => (
          <li key={t.seq} className="brokaid-claim-timeline__item">
            <div className="brokaid-claim-timeline__meta">
              <span className="brokaid-claim-timeline__kind">{t.kind}</span>
              <span className="brokaid-claim-timeline__ts">
                {formatClaimTs(t.ts)}
              </span>
            </div>
            <p className="brokaid-claim-timeline__body">{t.summary}</p>
          </li>
        ))}
      </ol>

      <Link
        to="/c/ask"
        className="brokaid-btn brokaid-btn--secondary"
        data-testid="claim-detail-ask"
      >
        Add a message to your broker
      </Link>
    </section>
  );
}

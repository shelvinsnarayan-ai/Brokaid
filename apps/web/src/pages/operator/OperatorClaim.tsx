/**
 * Phase 13 — operator claim view with the coverage-guard-visible demo.
 *
 * Renders one claim + its timeline. The composer at the bottom drafts
 * a status update; on POST /api/o/claims/:id/status-update the response
 * either releases (green) OR shows the blocked span highlighted + a
 * recommended rewrite. This is the visible-demo beat.
 */
import { FormEvent, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import "../../styles/phase13.css";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface TimelineRow {
  seq: number;
  ts: string;
  kind: string;
  summary: string;
  mocked: boolean;
}
interface Detail {
  claim: {
    claim_id: string;
    claim_reference: string;
    status: string;
    urgency: string | null;
    claim_type: string | null;
    description: string;
    opened_at: string;
  };
  timeline: TimelineRow[];
}

interface ReleaseResponse {
  released: boolean;
  verdict: string;
  reason?: string | null;
  blocked_spans?: { rule_id: string; start: number; end: number; text: string }[];
  recommended_rewrite?: string | null;
  guard_visible?: boolean;
  event_id?: string;
  notification?: { dev_mode: boolean; subject: string };
}

function highlight(draft: string, spans: { start: number; end: number }[]) {
  if (!spans || !spans.length) return [{ text: draft, isBlocked: false, key: 0 }];
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  const out: { text: string; isBlocked: boolean; key: number }[] = [];
  let cursor = 0;
  sorted.forEach((s, i) => {
    if (s.start > cursor)
      out.push({ text: draft.slice(cursor, s.start), isBlocked: false, key: i * 2 });
    out.push({ text: draft.slice(s.start, s.end), isBlocked: true, key: i * 2 + 1 });
    cursor = s.end;
  });
  if (cursor < draft.length)
    out.push({ text: draft.slice(cursor), isBlocked: false, key: sorted.length * 2 });
  return out;
}

export function OperatorClaim() {
  const { claim_id } = useParams<{ claim_id: string }>();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [draft, setDraft] = useState(
    "You are covered for this event. We'll pay the full amount today."
  );
  const [lastResult, setLastResult] = useState<ReleaseResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const reload = async () => {
    if (!claim_id) return;
    // Use the client view for read (both routes are RLS-tenant safe for
    // an operator role because operator sessions bind app.tenant_id).
    // But phase-13 didn't add an operator claim-detail endpoint, so we
    // reuse GET /api/c/claims/:id which the operator's user_client_links
    // may not have — instead call a lightweight query via events. For
    // this demo scene we take the client route because seed_all wires
    // the operator against the same tenant and the browser can still
    // display timeline via the client route.
    const r = await fetch(`${API}/api/c/claims/${claim_id}`, {
      credentials: "include",
    });
    if (r.ok) setDetail(await r.json());
  };

  useEffect(() => {
    reload();
  }, [claim_id]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setLastResult(null);
    try {
      const r = await fetch(`${API}/api/o/claims/${claim_id}/status-update`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft }),
      });
      const j = (await r.json()) as ReleaseResponse;
      setLastResult(j);
      if (j.released) {
        setDraft("");
        reload();
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!detail) return <p style={{ padding: 24 }}>Loading claim…</p>;
  const parts =
    lastResult && lastResult.blocked_spans && lastResult.blocked_spans.length
      ? highlight(draft, lastResult.blocked_spans)
      : null;

  return (
    <section className="ledger-panel" data-testid="operator-claim-root">
      <header className="ledger-panel__head">
        <p className="ledger-panel__eyebrow">Claim · operator</p>
        <h1 className="ledger-panel__title">{detail.claim.claim_reference}</h1>
        <div className="brokaid-claim-detail__pills">
          <span
            className={`brokaid-claim-card__status brokaid-claim-card__status--${detail.claim.status}`}
          >
            {detail.claim.status}
          </span>
          {detail.claim.urgency ? (
            <span
              className={`brokaid-urgency brokaid-urgency--${detail.claim.urgency}`}
            >
              {detail.claim.urgency}
            </span>
          ) : null}
        </div>
      </header>

      <div className="ledger-panel__section">
        <h2>Client FNOL</h2>
        <blockquote className="brokaid-claim-detail__desc">
          {detail.claim.description}
        </blockquote>
      </div>

      <div className="ledger-panel__section">
        <h2>Timeline</h2>
        <ol className="brokaid-claim-timeline">
          {detail.timeline.map((t) => (
            <li key={t.seq} className="brokaid-claim-timeline__item">
              <div className="brokaid-claim-timeline__meta">
                <span className="brokaid-claim-timeline__kind">{t.kind}</span>
                {t.mocked ? (
                  <span className="brokaid-mocked-pill">MOCKED · INSURER</span>
                ) : null}
                <span className="brokaid-claim-timeline__ts">
                  {new Date(t.ts).toLocaleString()}
                </span>
              </div>
              <p className="brokaid-claim-timeline__body">{t.summary}</p>
            </li>
          ))}
        </ol>
      </div>

      <form
        className="ledger-panel__section brokaid-status-composer"
        onSubmit={submit}
        data-testid="status-update-form"
      >
        <h2>Add a message to the client</h2>
        <p className="brokaid-status-composer__hint">
          Coverage-language guard runs on every draft.
        </p>
        <textarea
          rows={5}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          data-testid="status-update-draft"
          disabled={submitting}
        />
        <button
          type="submit"
          className="brokaid-btn brokaid-btn--primary"
          disabled={submitting || !draft.trim()}
          data-testid="status-update-submit"
        >
          {submitting ? "Checking…" : "Send"}
        </button>

        {lastResult && !lastResult.released ? (
          <div
            className="brokaid-guard-block"
            data-testid="guard-block-visible"
          >
            <div className="brokaid-guard-block__head">
              GUARD BLOCK · {lastResult.verdict}
            </div>
            <p className="brokaid-guard-block__reason">{lastResult.reason}</p>
            {parts ? (
              <div className="brokaid-guard-block__draft">
                {parts.map((p) => (
                  <span
                    key={p.key}
                    className={
                      p.isBlocked
                        ? "brokaid-guard-block__span"
                        : ""
                    }
                    data-testid={p.isBlocked ? "offending-span" : undefined}
                  >
                    {p.text}
                  </span>
                ))}
              </div>
            ) : null}
            {lastResult.recommended_rewrite ? (
              <div
                className="brokaid-guard-block__rewrite"
                data-testid="recommended-rewrite"
              >
                <span className="brokaid-guard-block__rewrite-label">
                  Suggested rewrite
                </span>
                <p>{lastResult.recommended_rewrite}</p>
              </div>
            ) : null}
          </div>
        ) : null}

        {lastResult && lastResult.released ? (
          <div className="brokaid-guard-ok" data-testid="guard-ok">
            Message released and sent · notification queued
            {lastResult.notification?.dev_mode ? " (dev-mode)" : ""}.
          </div>
        ) : null}
      </form>
    </section>
  );
}

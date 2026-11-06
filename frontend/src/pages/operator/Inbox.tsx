/** R0.8 — /o/inbox: client threads with reply-by promises. Overdue first, answered last. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Basis, PageHead, getJson, muted } from "./stubKit";
import { StaffHelp } from "../../components/ledger/StaffHelp";

export interface ThreadRow {
  thread_id: string; client: string; subject: string; last_message: string; last_at: string;
  reply_by: string | null; reply_by_text: string; reply_by_basis: string; status: string;
  messages: { value: number; data_basis: string };
  claim?: { claim_id: string; label: string } | null;
}

const TONE: Record<string, string> = {
  Overdue: "var(--signal-red, #E5484D)", "Due today": "var(--signal-amber, #F5A524)",
  "Awaiting reply": "var(--slate)", Answered: "var(--signal-green, #3FB950)",
};

export function StatusPill({ s, testid }: { s: string; testid?: string }) {
  return (
    <span data-testid={testid} style={{ fontSize: 12, padding: "2px 8px", border: `1px solid ${TONE[s] || "var(--line)"}`,
      color: TONE[s] || "inherit", whiteSpace: "nowrap" }}>{s}</span>
  );
}

const grid = "inbox-grid";
const css = `.${grid}{display:grid;grid-template-columns:minmax(140px,1fr) minmax(200px,2.2fr) minmax(150px,1fr) 130px;gap:4px 16px;align-items:start}
@media (max-width:760px){.${grid}{grid-template-columns:1fr}.inbox-head{display:none!important}}`;

export function Inbox() {
  const [rows, setRows] = useState<ThreadRow[] | null>(null);
  useEffect(() => { void getJson("/api/o/inbox").then((r) => setRows(r.ok ? r.data.threads : [])); }, []);
  if (!rows) return <p data-testid="inbox-loading" style={muted}>Loading…</p>;
  return (
    <div data-testid="inbox-page" style={{ maxWidth: 1100, minWidth: 0 }}>
      <style>{css}</style>
      <PageHead eyebrow="WORK · INBOX" title="Inbox">
        <p style={muted} data-testid="inbox-count">{rows.length} client threads. Overdue first.<Basis b="registry" /></p>
      </PageHead>
      {!rows.length ? <p style={muted} data-testid="inbox-empty">No client messages yet.</p> : (
        <div data-testid="inbox-table" role="table">
          <div className={`${grid} inbox-head`} style={{ ...muted, padding: "6px 10px" }} role="row">
            <span>Client</span><span>Last message</span><span>Reply by <StaffHelp k="reply_by" testid="inbox-help-reply-by" /></span><span>Status</span>
          </div>
          {rows.map((t, i) => (
            <Link key={t.thread_id} to={`/o/inbox/${encodeURIComponent(t.thread_id)}`} className={grid} role="row"
              data-testid={`inbox-row-${i}`}
              style={{ padding: "10px", borderTop: "1px solid var(--line, #1E2E5A)", color: "inherit", textDecoration: "none" }}>
              <span data-testid={`inbox-client-${i}`}>{t.client}
                {t.claim ? <span data-testid={`inbox-claim-tag-${i}`} style={{ display: "block", fontSize: 12, marginTop: 2 }}>
                  <span style={{ border: "1px solid var(--signal-amber, #F5A524)", padding: "0 6px", marginRight: 6 }}>Claim</span>{t.claim.label}</span> : null}
              </span>
              <span style={{ overflowWrap: "anywhere" }}>{t.last_message}
                <span style={{ ...muted, display: "block" }}>{t.messages.value} message{t.messages.value === 1 ? "" : "s"}<Basis b={t.messages.data_basis} /></span>
              </span>
              <span data-testid={`inbox-reply-by-${i}`} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}>
                {t.reply_by_text}<span style={{ ...muted, display: "block" }}>+1 business day</span>
              </span>
              <span><StatusPill s={t.status} testid={`inbox-status-${i}`} /></span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

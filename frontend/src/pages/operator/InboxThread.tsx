/** R0.8 — /o/inbox/:thread_id: full conversation + reply (AE and Principal). Operator reads only. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fmtDateTime } from "../../lib/format";
import { API, PageHead, card, getJson, muted } from "./stubKit";
import { StatusPill, type ThreadRow } from "./Inbox";
import { ClaimAttachments, type Att } from "../../components/ClaimAttachments";
import { AttachTray } from "../client/AttachTray";
import { useAttachments } from "../client/useAttachments";
import type { Limits } from "../client/attachUpload";
import { QuoteFields, quoteLine, useQuotes, type Quote } from "./QuoteFields";
import { StaffHelp } from "../../components/ledger/StaffHelp";

interface Msg { who: "client" | "broker" | "pending"; text: string; at: string; author: string | null; attachments?: Att[]; claim_id?: string | null }
interface Approval {
  approval_id: string; body: string; status: "pending" | "approved" | "rejected" | "withdrawn" | "refused"; requested_at: string;
  requested_by: string; decided_by?: string | null; reason?: string | null; draft_id?: string; files?: (Att & { quote?: Quote | null })[];
}
export interface Prefill { text: string; draft_id?: string; files?: (Att & { quote?: Quote | null })[]; approval_id: string }
const STATUS: Record<Approval["status"], string> = { pending: "Awaiting Principal approval", approved: "Approved", rejected: "Rejected by a Principal. Files kept as drafts",
  withdrawn: "Withdrawn. Files kept as drafts", refused: "Not sent" };
interface Thread extends ThreadRow { conversation: Msg[]; can_reply: boolean; kept: { kept_at: string; late: boolean } | null; advice_approvals: Approval[]; me?: { user_id: string; role: string }; attachment_limits?: (Limits & { closed?: boolean }) | null }

const MAX = 4000;

function Bubble({ m, i }: { m: Msg; i: number }) {
  const mine = m.who === "broker";
  return (
    <li data-testid={`thread-msg-${i}`} style={{ alignSelf: mine ? "flex-end" : "flex-start", maxWidth: "min(620px, 100%)",
      border: "1px solid var(--line, #1E2E5A)", padding: "10px 12px", background: mine ? "rgba(255,255,255,0.04)" : "transparent" }}>
      <div style={muted}>{m.who === "client" ? "Client" : m.who === "pending" ? "Pending" : m.author || "Broker"} · {fmtDateTime(m.at)}</div>
      <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", marginTop: 4 }}>{m.text}</div>
      {m.claim_id ? <ClaimAttachments atts={m.attachments || []} base={`${API}/api/o/claims/${m.claim_id}/files`} testid={`thread-msg-att-${i}`} /> : null}
    </li>
  );
}

async function post(path: string, body: unknown) {
  return fetch(`${API}${path}`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

/** R1.8: personal-advice replies wait for a Principal; the approval is bound to the exact text and (R3.7) the exact files. */
function Approvals({ id, list, me, onDone, onEdit }: { id: string; list: Approval[]; me?: { user_id: string; role: string }; onDone: () => void; onEdit: (p: Prefill) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!list?.length) return null;
  async function act(a: Approval, what: "approve" | "reject" | "withdraw") {
    setBusy(a.approval_id); setErr(null);
    const r = await post(`/api/o/inbox/${encodeURIComponent(id)}/advice-approval/${a.approval_id}/${what}`, {});
    setBusy(null);
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      const d = j.detail;
      setErr(typeof d?.detail === "string" ? d.detail : r.status === 403 ? (typeof d === "string" ? d : "Only a Principal can approve personal advice.") : "Couldn't update the approval.");
      onDone(); return;
    }
    onDone();
  }
  async function send(a: Approval) {
    const r = await post(`/api/o/inbox/${encodeURIComponent(id)}/reply`, { body: a.body, idempotency_key: `approved-${a.approval_id}`, approval_id: a.approval_id });
    if (!r.ok) { setErr("Reply not sent."); return; }
    onDone();
  }
  const mine = (a: Approval) => me?.user_id === a.requested_by;
  return (
    <section style={{ ...card, marginBottom: 12 }} data-testid="advice-approvals">
      <p style={muted}>Personal-advice replies (Principal approval required; a Statement of Advice may be needed)</p>
      {list.map((a) => (
        <div key={a.approval_id} data-testid={`advice-approval-${a.approval_id}`} style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid rgba(127,140,165,.3)" }}>
          <div style={{ whiteSpace: "pre-wrap" }}>{a.body}</div>
          {a.files?.length ? (
            <div data-testid={`advice-approval-files-${a.approval_id}`} style={{ marginTop: 6 }}>
              <span style={muted}>{a.files.length} file{a.files.length === 1 ? "" : "s"} {a.status === "pending" ? "held with this reply (not visible to the client)" : ""}</span>
              <ClaimAttachments atts={a.files} base={`${API}/api/o/inbox/${encodeURIComponent(id)}/advice-approval/${a.approval_id}/files`} testid={`advice-approval-att-${a.approval_id}`} />
              {a.files.filter((f) => f.quote).map((f) => <p key={f.file_id} style={{ ...muted, margin: "4px 0 0" }} data-testid={`advice-approval-quote-${f.file_id}`}>Quote summary for the client: {quoteLine(f.quote!)}</p>)}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
            <span style={muted} data-testid={`advice-approval-status-${a.approval_id}`}>{STATUS[a.status]}{a.reason ? `: ${a.reason}` : ""}</span>
            {a.status === "pending" && me?.role === "principal" && !mine(a) ? <>
              <button className="k-btn k-btn--primary" disabled={busy === a.approval_id} onClick={() => void act(a, "approve")} data-testid={`advice-approve-${a.approval_id}`}>
                {a.files?.length ? "Approve and send text + files" : "Approve (Principal)"}</button>
              <button className="k-btn" disabled={busy === a.approval_id} onClick={() => void act(a, "reject")} data-testid={`advice-reject-${a.approval_id}`}>Reject</button>
            </> : null}
            {a.status === "pending" && mine(a) ? <button className="k-btn" onClick={() => void act(a, "withdraw")} data-testid={`advice-withdraw-${a.approval_id}`}>Withdraw to edit</button> : null}
            {a.status === "approved" ? <button className="k-btn k-btn--primary" onClick={() => void send(a)} data-testid={`advice-send-${a.approval_id}`}>Send approved reply</button> : null}
            {["rejected", "withdrawn", "refused"].includes(a.status) && mine(a) ? (
              <button className="k-btn" onClick={() => onEdit({ text: a.body, draft_id: a.draft_id, files: a.files, approval_id: a.approval_id })} data-testid={`advice-edit-${a.approval_id}`}>Edit and resend</button>
            ) : null}
          </div>
        </div>
      ))}
      {err && <p role="alert" style={muted} data-testid="advice-approval-error">{err}</p>}
    </section>
  );
}

function ReplyBox({ id, onSent, limits, claimId, prefill }: { id: string; onSent: () => void; limits?: (Limits & { closed?: boolean }) | null; claimId?: string | null; prefill?: Prefill | null }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [pAdvice, setPAdvice] = useState<string | null>(null);
  const key = useRef(crypto.randomUUID());
  const base = `${API}/api/o/claims/${claimId}/thread/attachments`;
  const att = useAttachments(base, limits || undefined, true);
  const canAttach = !!claimId && !!limits && !limits.closed;
  const q = useQuotes(att.items);
  useEffect(() => {
    if (!prefill) return;
    setText(prefill.text); setPAdvice(null); key.current = crypto.randomUUID();
    if (prefill.draft_id && prefill.files?.length) {
      const fbase = `${API}/api/o/inbox/${encodeURIComponent(id)}/advice-approval/${prefill.approval_id}/files`;
      att.load(prefill.draft_id, prefill.files.map((f) => ({ ...f, url: `${fbase}/${f.file_id}${f.has_preview ? "?preview=true" : ""}` })));
      q.seed(Object.fromEntries(prefill.files.filter((f) => f.quote).map((f) => [f.file_id, f.quote!])));
    }
  }, [prefill]);
  async function send() {
    setBusy(true); setErr(null); setPAdvice(null);
    const files = att.fileIds.length ? { file_ids: att.fileIds, draft_id: att.msgId, labels: att.labelMap, quotes: q.payload } : {};
    const r = await post(`/api/o/inbox/${encodeURIComponent(id)}/reply`, { body: text, idempotency_key: key.current, ...files });
    setBusy(false);
    if (r.status === 409) {
      const j = await r.json().catch(() => ({}));
      if (j.detail?.code === "PersonalAdvice") { setPAdvice(j.detail.detail); return; }
      if (j.detail?.code === "claim_closed") { setErr("This claim is closed, so files can't be attached."); return; }
    }
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      setErr(r.status === 403 ? "Your role can read this thread but not reply." : j.detail?.detail && typeof j.detail.detail === "string" ? j.detail.detail : "Reply not sent. Check the text and try again.");
      return;
    }
    key.current = crypto.randomUUID(); setText(""); att.reset(); q.reset(); onSent();
  }
  async function askApproval() {
    const files = att.fileIds.length ? { file_ids: att.fileIds, draft_id: att.msgId, labels: att.labelMap, quotes: q.payload } : {};
    const r = await post(`/api/o/inbox/${encodeURIComponent(id)}/advice-approval`, { body: text.trim(), ...files });
    if (!r.ok) { const j = await r.json().catch(() => ({})); setErr(typeof j.detail?.detail === "string" ? j.detail.detail : "Couldn't request approval."); return; }
    setPAdvice(null); setText(""); att.reset(); q.reset(); onSent();
  }
  const ok = (text.trim().length > 0 || att.fileIds.length > 0) && text.length <= MAX && !att.busy && !att.blocked;
  return (
    <section style={card} data-testid="thread-reply">
      <label htmlFor="reply-body" style={muted}>Reply to the client</label>
      <textarea id="reply-body" value={text} onChange={(e) => setText(e.target.value)} rows={5} maxLength={MAX}
        data-testid="thread-reply-input" style={{ width: "100%", boxSizing: "border-box", marginTop: 6 }} />
      {canAttach ? (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
          <AttachTray items={att.items} base={base} onRemove={att.remove} onRetry={att.retry} onLabel={att.relabel} testid="thread-attach" />
          <QuoteFields q={q} />
          {att.errors.length ? <ul className="cat-errs" role="alert" data-testid="thread-attach-errors">{att.errors.map((x, i) => <li key={i}>{x}</li>)}</ul> : null}
          <label className="k-btn" style={{ alignSelf: "flex-start", cursor: "pointer" }} data-testid="thread-attach-button">
            <input type="file" multiple accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif,application/pdf,.pdf"
              style={{ position: "absolute", width: 1, height: 1, opacity: 0 }}
              onChange={(e) => { att.add(e.target.files); e.target.value = ""; }} data-testid="thread-attach-input" />
            Attach photos or PDFs
          </label>
          <span style={muted} data-testid="thread-attach-hint">Up to 5 files · photos 10 MB (location removed) · PDFs 20 MB, no scripts or passwords</span>
        </div>
      ) : null}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8, gap: 8 }}>
        <span style={muted} data-testid="thread-reply-count">{text.length} / {MAX}</span>
        <button className="k-btn k-btn--primary" disabled={!ok || busy} onClick={send} data-testid="thread-reply-send">
          {busy ? "Sending…" : att.busy ? "Uploading…" : "Send"}
        </button>
      </div>
      {err && <p role="alert" style={{ ...muted, marginTop: 8 }} data-testid="thread-reply-error">{err}</p>}
      {pAdvice && (
        <div role="alert" data-testid="thread-reply-personal-advice" style={{ marginTop: 8, borderLeft: "3px solid #E5A100", paddingLeft: 10 }}>
          <p style={{ margin: 0 }}>{pAdvice}</p>
          <button className="k-btn" onClick={() => void askApproval()} data-testid="thread-reply-request-approval" style={{ marginTop: 6 }}>Ask a Principal to approve</button>
        </div>
      )}
    </section>
  );
}

export function InboxThread() {
  const { thread_id = "" } = useParams();
  const [t, setT] = useState<Thread | null>(null);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [missing, setMissing] = useState(false);
  const load = useCallback(() => getJson(`/api/o/inbox/${encodeURIComponent(thread_id)}`)
    .then((r) => (r.ok ? setT(r.data) : setMissing(true))), [thread_id]);
  useEffect(() => { void load(); }, [load]);
  if (missing) return <p style={muted} data-testid="thread-missing">Thread not found. <Link to="/o/inbox">Back to Inbox</Link></p>;
  if (!t) return <p style={muted} data-testid="thread-loading">Loading…</p>;
  return (
    <div data-testid="thread-page" style={{ maxWidth: 820, minWidth: 0 }}>
      <Link to="/o/inbox" style={muted} data-testid="thread-back">← Inbox</Link>
      <PageHead eyebrow={`INBOX · ${t.client.toUpperCase()}`} title={t.subject}>
        <p style={{ ...muted, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <StatusPill s={t.status} testid="thread-status" />
          <span data-testid="thread-reply-by">Reply by {t.reply_by_text} (+1 business day) <StaffHelp k="reply_by" testid="thread-help-reply-by" /></span>
          {t.kept && <span data-testid="thread-kept">{t.kept.late ? "Answered late" : "Answered on time"}</span>}
        </p>
      </PageHead>
      <ol data-testid="thread-conversation" style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: 10, marginBottom: 20 }}>
        {t.conversation.map((m, i) => <Bubble key={i} m={m} i={i} />)}
      </ol>
      <Approvals id={t.thread_id} list={t.advice_approvals} me={t.me} onDone={() => void load()} onEdit={setPrefill} />
      {t.can_reply ? <ReplyBox id={t.thread_id} onSent={() => void load()} limits={t.attachment_limits} prefill={prefill}
        claimId={t.thread_id.startsWith("claim-") ? t.thread_id.slice(6) : null} />
        : <p style={muted} data-testid="thread-read-only">Read only. Replies come from the account executive or the Principal.</p>}
    </div>
  );
}

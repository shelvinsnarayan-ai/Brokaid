/** R0.8 — /o/inbox/:thread_id: full conversation + reply (AE and Principal). Operator reads only. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fmtDateTime } from "../../lib/format";
import { API, PageHead, card, getJson, muted } from "./stubKit";
import { StatusPill, type ThreadRow } from "./Inbox";

interface Msg { who: "client" | "broker" | "pending"; text: string; at: string; author: string | null }
interface Thread extends ThreadRow { conversation: Msg[]; can_reply: boolean; kept: { kept_at: string; late: boolean } | null }

const MAX = 4000;

function Bubble({ m, i }: { m: Msg; i: number }) {
  const mine = m.who === "broker";
  return (
    <li data-testid={`thread-msg-${i}`} style={{ alignSelf: mine ? "flex-end" : "flex-start", maxWidth: "min(620px, 100%)",
      border: "1px solid var(--line, #1E2E5A)", padding: "10px 12px", background: mine ? "rgba(255,255,255,0.04)" : "transparent" }}>
      <div style={muted}>{m.who === "client" ? "Client" : m.who === "pending" ? "Pending" : m.author || "Broker"} · {fmtDateTime(m.at)}</div>
      <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", marginTop: 4 }}>{m.text}</div>
    </li>
  );
}

function ReplyBox({ id, onSent }: { id: string; onSent: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const key = useRef(crypto.randomUUID());
  async function send() {
    setBusy(true); setErr(null);
    const r = await fetch(`${API}/api/o/inbox/${encodeURIComponent(id)}/reply`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: text, idempotency_key: key.current }),
    });
    setBusy(false);
    if (!r.ok) { setErr(r.status === 403 ? "Your role can read this thread but not reply." : "Reply not sent. Check the text and try again."); return; }
    key.current = crypto.randomUUID(); setText(""); onSent();
  }
  const ok = text.trim().length > 0 && text.length <= MAX;
  return (
    <section style={card} data-testid="thread-reply">
      <label htmlFor="reply-body" style={muted}>Reply to the client</label>
      <textarea id="reply-body" value={text} onChange={(e) => setText(e.target.value)} rows={5} maxLength={MAX}
        data-testid="thread-reply-input" style={{ width: "100%", boxSizing: "border-box", marginTop: 6 }} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8, gap: 8 }}>
        <span style={muted} data-testid="thread-reply-count">{text.length} / {MAX}</span>
        <button className="k-btn k-btn--primary" disabled={!ok || busy} onClick={send} data-testid="thread-reply-send">
          {busy ? "Sending…" : "Send"}
        </button>
      </div>
      {err && <p role="alert" style={{ ...muted, marginTop: 8 }} data-testid="thread-reply-error">{err}</p>}
    </section>
  );
}

export function InboxThread() {
  const { thread_id = "" } = useParams();
  const [t, setT] = useState<Thread | null>(null);
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
          <span data-testid="thread-reply-by">Reply by {t.reply_by_text} (+1 business day)</span>
          {t.kept && <span data-testid="thread-kept">{t.kept.late ? "Answered late" : "Answered on time"}</span>}
        </p>
      </PageHead>
      <ol data-testid="thread-conversation" style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: 10, marginBottom: 20 }}>
        {t.conversation.map((m, i) => <Bubble key={i} m={m} i={i} />)}
      </ol>
      {t.can_reply ? <ReplyBox id={t.thread_id} onSent={() => void load()} />
        : <p style={muted} data-testid="thread-read-only">Read only. Replies come from the account executive or the Principal.</p>}
    </div>
  );
}

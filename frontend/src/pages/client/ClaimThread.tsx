import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { cget, Skel } from "./api";
import { useUnreadStore } from "../../state/unreadStore";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface Msg { who: string; text: string; at: string; author?: string | null; reply_id?: string | null; gaw?: string | null }
interface Thread {
  claim: { claim_id: string; claim_reference: string; label: string; stage_label: string };
  thread_id: string; exists: boolean; messages: Msg[]; unread_reply_ids: string[]; read_only: boolean;
  closed_note: string | null; can_post: boolean; reply_by_text: string | null;
}

export default function ClaimThread() {
  const { claimId } = useParams();
  const [data, setData] = useState<Thread | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const { refresh, markSeen } = useUnreadStore();
  const load = () => cget<Thread>(`c/claims/${claimId}/thread`).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { void load(); }, [claimId]);
  useEffect(() => {
    if (data?.unread_reply_ids.length) void refresh().then(() => markSeen([data.thread_id]));
  }, [data?.thread_id, data?.unread_reply_ids.length]);
  const send = async () => {
    setBusy(true); setErr(null);
    const r = await fetch(`${API}/api/c/claims/${claimId}/thread`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }),
    });
    setBusy(false);
    if (!r.ok) { const b = await r.json().catch(() => ({})); setErr(b?.detail?.detail || b?.detail || "Couldn't send. Please try again."); return; }
    setText(""); void load();
  };
  if (err && !data) return <p className="mer-empty" role="alert" style={{ margin: 16 }} data-testid="mer-claim-thread-error">{err}</p>;
  if (!data) return <div style={{ padding: 16 }}><Skel h={80} n={1} /><Skel /></div>;
  return (
    <div data-testid="mer-claim-thread">
      <div className="mer-hero">
        <Link to="/c/messages" style={{ color: "#fff", opacity: .8, fontSize: 13, textDecoration: "none" }} data-testid="mer-claim-thread-back">‹ Messages</Link>
        <h1 className="mer-h" style={{ margin: "8px 0 0", fontSize: 18 }}>{data.claim.label}</h1>
        <p style={{ margin: "2px 0 0", fontSize: 13, opacity: .85 }}>{data.claim.stage_label} · {data.claim.claim_reference}</p>
        <Link to={`/c/claims/${data.claim.claim_id}`} className="mer-copy mer-copy--dark" style={{ marginTop: 10, display: "inline-flex" }}
          data-testid="mer-claim-thread-tracker">View claim tracker</Link>
      </div>
      <section className="mer-sec" aria-live="polite">
        {!data.messages.length ? <p className="mer-empty" data-testid="mer-claim-thread-empty">Start a conversation about this claim. Your broker will reply here.</p> : null}
        {data.messages.map((m, k) => {
          const isNew = !!m.reply_id && data.unread_reply_ids.includes(m.reply_id);
          return (
            <div key={k} className={`mer-bubble mer-bubble--${m.who}${isNew ? " mer-bubble--new" : ""}`} data-testid={`mer-claim-msg-${k}`}>
              {isNew ? <span className="mer-badge" style={{ marginTop: 0, marginBottom: 4 }} data-testid={`mer-claim-msg-new-${k}`}>New</span> : null}
              <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{m.text}</p>
              {m.gaw ? <p className="mer-row__s" style={{ marginTop: 6 }} data-testid={`mer-claim-msg-gaw-${k}`}>{m.gaw}</p> : null}
              <p className="mer-row__s" style={{ marginTop: 4 }}>{m.who === "broker" ? m.author || "Your broker" : "You"} · {new Date(m.at).toLocaleString("en-AU", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</p>
            </div>
          );
        })}
        {data.reply_by_text ? <p className="mer-row__s" data-testid="mer-claim-thread-reply-by">{data.reply_by_text}</p> : null}
        {data.read_only ? <p className="mer-banner" data-testid="mer-claim-thread-closed">{data.closed_note}</p>
          : data.can_post ? (
            <form className="mer-compose" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void send(); }}>
              <label htmlFor="claim-msg" className="sr-only">Message about this claim</label>
              <textarea id="claim-msg" rows={3} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)}
                placeholder="Write a message about this claim" data-testid="mer-claim-thread-input" />
              <button type="submit" className="mer-btn" disabled={busy || !text.trim()} data-testid="mer-claim-thread-send">{busy ? "Sending…" : "Send"}</button>
            </form>
          ) : <p className="mer-row__s" data-testid="mer-claim-thread-noauth">Someone with claims authority for your business can message about this claim.</p>}
        {err ? <p className="mer-empty" role="alert" data-testid="mer-claim-thread-send-error">{err}</p> : null}
      </section>
    </div>
  );
}

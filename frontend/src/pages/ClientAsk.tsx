/**
 * ClientAsk — /c/ask
 *
 * Functional chat with the client's broker. Every response is
 * structured: broker attribution row, response body, next step row,
 * optional "Verified by your brokerage" microtoast when the classifier
 * marks the draft as factual. HITL responses render a placeholder.
 */
import { useEffect, useRef, useState } from "react";
import { useUnreadStore } from "../state/unreadStore";

const API = import.meta.env.VITE_API_BASE || "";

type ThreadItem =
  | { who: "client"; text: string; ts?: string }
  | {
      who: "broker";
      text: string;
      attribution?: string | null;
      next_step?: string | null;
      verified?: boolean;
      ts?: string;
      reply_id?: string | null;
      gaw?: string | null;
    }
  | { who: "broker_pending"; text: string; ts?: string };

type AskResponse = {
  envelope_id: string;
  released: boolean;
  verdict: string;
  draft: {
    response_draft: string;
    broker_attribution: string;
    next_step: string;
    detected_coverage_question: boolean;
  };
  classifier_verdict: { advice_classification?: string } | null;
};

export function ClientAsk() {
  const [thread, setThread] = useState<ThreadItem[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement | null>(null);

  const [replyBy, setReplyBy] = useState<string | null>(null);
  // Replies unread when this visit began (snapshot before marking seen); this visit only.
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const loadThread = async () => {
    try {
      const r = await fetch(`${API}/api/c/ask/thread`, { credentials: "include" });
      if (r.ok) {
        const b = await r.json();
        setThread(b.thread || []);
        setReplyBy(b.reply_by_text || null);
        const u = useUnreadStore.getState();
        await u.refresh();
        const ids = useUnreadStore.getState().threads.flatMap((t) => t.reply_ids || []);
        if (ids.length) {
          setFresh((prev) => new Set([...prev, ...ids]));
          await u.markSeen();
        }
      }
    } catch {
      // best effort
    }
  };
  useEffect(() => {
    void loadThread();
    const t = window.setInterval(() => void loadThread(), 30000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (listRef.current)
      listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [thread]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const msg = input.trim();
    if (!msg || sending) return;
    setSending(true);
    setErr(null);
    setThread((t) => [...t, { who: "client", text: msg, ts: new Date().toISOString() }]);
    setInput("");
    try {
      const r = await fetch(`${API}/api/c/ask/message`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });
      if (!r.ok) {
        setErr(`${r.status}: ${await r.text()}`);
        setThread((t) => [
          ...t,
          {
            who: "broker_pending",
            text: "Your broker will get back to you shortly.",
            ts: new Date().toISOString(),
          },
        ]);
        return;
      }
      const body = (await r.json()) as AskResponse;
      if (body.released) {
        setThread((t) => [
          ...t,
          {
            who: "broker",
            text: body.draft.response_draft,
            attribution: body.draft.broker_attribution,
            next_step: body.draft.next_step,
            verified:
              body.classifier_verdict?.advice_classification === "factual",
            ts: new Date().toISOString(),
          },
        ]);
      } else {
        await loadThread();
      }
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="brokaid-tab brokaid-tab--ask" data-testid="tab-ask-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Ask</p>
        <h1 className="brokaid-tab__title">Your broker line</h1>
        <p className="brokaid-tab__lede">
          Plain-English answers, in your broker's voice. Coverage
          questions get routed to a human for a written reply.
        </p>
      </header>

      {replyBy && (
        <p className="brokaid-tab__lede" data-testid="ask-reply-by">
          Your broker will reply by {replyBy}.
        </p>
      )}

      <ol
        className="brokaid-ask"
        data-testid="ask-thread"
        ref={listRef}
      >
        {thread.length === 0 && (
          <li className="brokaid-ask__msg brokaid-ask__msg--broker">
            <div className="brokaid-ask__meta">Your broker</div>
            <div className="brokaid-ask__body">
              Hi. This is your broker's line. What do you need?
            </div>
          </li>
        )}
        {thread.map((m, i) => {
          const key = m.ts ?? `local-${i}`;
          if (m.who === "client") {
            return (
              <li
                key={key}
                className="brokaid-ask__msg brokaid-ask__msg--client"
                data-testid={`ask-msg-client-${i}`}
              >
                <div className="brokaid-ask__body">{m.text}</div>
              </li>
            );
          }
          if (m.who === "broker_pending") {
            return (
              <li
                key={key}
                className="brokaid-ask__msg brokaid-ask__msg--pending"
                data-testid={`ask-msg-pending-${i}`}
              >
                <div className="brokaid-ask__meta">Waiting for your broker</div>
                <div className="brokaid-ask__body">{m.text}</div>
              </li>
            );
          }
          const isNew = !!m.reply_id && fresh.has(m.reply_id);
          return (
            <li
              key={key}
              className={`brokaid-ask__msg brokaid-ask__msg--broker${isNew ? " brokaid-ask__msg--new" : ""}`}
              data-testid={`ask-msg-broker-${i}`}
              data-new={isNew ? "true" : undefined}
            >
              {isNew && <span className="brokaid-sr-only" data-testid={`ask-new-${i}`}>New</span>}
              {m.attribution && (
                <div
                  className="brokaid-ask__attribution"
                  data-testid={`ask-attribution-${i}`}
                >
                  {m.attribution}
                </div>
              )}
              <div className="brokaid-ask__body">{m.text}</div>
              {m.gaw ? <p className="brokaid-ask__gaw" data-testid={`ask-gaw-${i}`} style={{ fontSize: 12, opacity: 0.8, marginTop: 6 }}>{m.gaw}</p> : null}
              {m.next_step && (
                <div
                  className="brokaid-ask__next-step"
                  data-testid={`ask-next-step-${i}`}
                >
                  <span className="brokaid-ask__next-step-label">
                    Next
                  </span>{" "}
                  {m.next_step}
                </div>
              )}
              {m.verified && (
                <div
                  className="brokaid-ask__verified"
                  data-testid={`ask-verified-${i}`}
                >
                  Verified by your brokerage
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {err && (
        <p
          className="brokaid-quote-card__summary"
          data-testid="ask-error"
        >
          {err}
        </p>
      )}

      <form
        className="brokaid-ask__form"
        onSubmit={send}
        aria-label="Send a message to your broker"
      >
        <input
          type="text"
          placeholder="Type a question…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={sending}
          data-testid="ask-input"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="brokaid-btn brokaid-btn--primary"
          data-testid="ask-send"
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </form>
    </section>
  );
}

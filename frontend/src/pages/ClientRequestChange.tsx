/**
 * ClientRequestChange — /c/cover/:policy_id/request-change
 *
 * Accept-fast: on submit we send an idempotency key (uuid v4) and
 * expect a <2s 200 with state="received". The server runs intake +
 * placement + mocked carrier asynchronously. We navigate to the
 * /pending screen which polls and advances once the operator
 * approves the Shadow-Mode gate.
 *
 * Three rapid-fire taps with the same idempotency key return the
 * SAME envelope_id — no duplicate queue cards.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { NextSteps } from "./client/Explain";

const API = import.meta.env.VITE_API_BASE || "";

type SceneState = "idle" | "sending" | "received" | "error";

function uuidv4(): string {
  if (typeof crypto !== "undefined" && (crypto as any).randomUUID) {
    return (crypto as any).randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function ClientRequestChange() {
  const { policy_id = "" } = useParams();
  const navigate = useNavigate();
  const [raw, setRaw] = useState("");
  const [state, setState] = useState<SceneState>("idle");
  const [errMsg, setErrMsg] = useState<string | null>(null);
  // Lock the idempotency key to the current draft content. A fresh
  // key is minted on first keystroke after a successful submit.
  const idemRef = useRef<string>(uuidv4());

  useEffect(() => {
    if (state === "idle") {
      idemRef.current = uuidv4();
    }
  }, [state === "idle" ? raw : null, state]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!raw.trim()) return;
    setState("sending");
    setErrMsg(null);
    try {
      const r = await fetch(
        `${API}/api/c/matters/${policy_id}/request-change`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            raw_message: raw,
            idempotency_key: idemRef.current,
          }),
        }
      );
      if (!r.ok) {
        const t = await r.text();
        setErrMsg(`${r.status}: ${t}`);
        setState("error");
        return;
      }
      const body = await r.json();
      setState("received");
      // Show "Received" for a beat, then forward to /pending where
      // the client sees the live "With your broker" waiting state.
      window.setTimeout(() => {
        navigate(
          `/c/cover/${policy_id}/pending?env=${body?.envelope_id ?? ""}`
        );
      }, 600);
    } catch (err: any) {
      setErrMsg(String(err));
      setState("error");
    }
  }

  return (
    <section className="brokaid-tab" data-testid="request-change-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Request a change</p>
        <h1 className="brokaid-tab__title">Tell us what you need</h1>
        <p className="brokaid-tab__lede">
          Describe the change in your own words. Your broker will handle
          the wording, the market, and the paperwork.
        </p>
      </header>
      <NextSteps flow="change" />

      {state === "error" && (
        <EmptyState
          title="Something went wrong"
          body={errMsg || "Please try again."}
          testId="request-change-error"
        />
      )}

      <form
        className="brokaid-request-form"
        onSubmit={submit}
        data-testid="request-change-form"
      >
        <label
          htmlFor="raw_message"
          className="brokaid-request-form__label"
        >
          What would you like to change?
        </label>
        <textarea
          id="raw_message"
          name="raw_message"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          className="brokaid-request-form__textarea"
          placeholder="e.g. We took on a bigger project. Please raise our public liability from $10M to $20M."
          rows={6}
          disabled={state === "sending" || state === "received"}
          data-testid="request-change-textarea"
        />
        <button
          type="submit"
          className="brokaid-btn brokaid-btn--primary"
          disabled={state === "sending" || state === "received"}
          data-testid="request-change-submit"
        >
          {state === "sending"
            ? "Sending…"
            : state === "received"
            ? "Received ✓"
            : "Send to my broker"}
        </button>
      </form>

      {(state === "sending" || state === "received") && (
        <div
          className="brokaid-request-progress"
          data-testid="request-change-progress"
        >
          <p className="brokaid-request-progress__label">
            {state === "received"
              ? "Received — your broker is on it."
              : "Sending…"}
          </p>
          <div className="brokaid-request-progress__bar">
            <div className="brokaid-request-progress__bar-fill" />
          </div>
        </div>
      )}
    </section>
  );
}

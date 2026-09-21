/**
 * ClientRequestChange — /c/cover/:policy_id/request-change
 *
 * The client submits a plain-language change request. Backend fires
 * intAIke → plAIce → MOCKED carrier quote. On success, we redirect
 * to the pending-quote card.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";

const API = import.meta.env.VITE_API_BASE || "";

type SceneState =
  | "idle"
  | "sending"
  | "intake_running"
  | "placement_running"
  | "awaiting_operator"
  | "error";

export function ClientRequestChange() {
  const { policy_id = "" } = useParams();
  const navigate = useNavigate();
  const [raw, setRaw] = useState("");
  const [state, setState] = useState<SceneState>("idle");
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

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
          body: JSON.stringify({ raw_message: raw }),
        }
      );
      if (!r.ok) {
        const t = await r.text();
        setErrMsg(`${r.status}: ${t}`);
        setState("error");
        return;
      }
      const body = await r.json();
      // The API returns 200 as soon as the request is on file; the
      // operator's approval + carrier quote lands via async status
      // polling. Send the client to the pending screen immediately so
      // they see a state ("With your broker — usually under an hour")
      // instead of a hanging "Working…" button.
      setState("awaiting_operator");
      navigate(`/c/cover/${policy_id}/pending?env=${body?.envelope_id ?? ""}`);
    } catch (err: any) {
      setErrMsg(String(err));
      setState("error");
    }
  }

  const stepLabels: Record<Exclude<SceneState, "idle" | "error">, string> = {
    sending: "Sending to your broker…",
    intake_running: "Reading your request…",
    placement_running: "Drafting the submission…",
    awaiting_operator: "Quote is ready — routing to your broker.",
  };

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
          disabled={state !== "idle" && state !== "error"}
          data-testid="request-change-textarea"
        />
        <button
          type="submit"
          className="brokaid-btn brokaid-btn--primary"
          disabled={
            state !== "idle" && state !== "error"
          }
          data-testid="request-change-submit"
        >
          {state === "idle" || state === "error"
            ? "Send to my broker"
            : "Working…"}
        </button>
      </form>

      {state !== "idle" && state !== "error" && (
        <div
          className="brokaid-request-progress"
          data-testid="request-change-progress"
        >
          <p className="brokaid-request-progress__label">
            {stepLabels[state]}
          </p>
          <div className="brokaid-request-progress__bar">
            <div className="brokaid-request-progress__bar-fill" />
          </div>
        </div>
      )}
    </section>
  );
}

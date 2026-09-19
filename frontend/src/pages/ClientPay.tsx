/**
 * ClientPay — /c/cover/:policy_id/pay/:envelope_id
 *
 * MOCKED payment (PayTo AU / PayNow SG). The button hits the backend
 * which emits PaymentMandateCreated + PaymentInitiated + PaymentReceived
 * on the ledger, all with mocked=true. Renders MOCKED · PAYMENT pill.
 */
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { usePack } from "../state/meStore";

const API = import.meta.env.VITE_API_BASE || "";

export function ClientPay() {
  const { policy_id = "", envelope_id = "" } = useParams();
  const navigate = useNavigate();
  const pack = usePack();
  const [state, setState] = useState<"idle" | "paying" | "paid" | "error">(
    "idle"
  );
  const [receipt, setReceipt] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const rail = pack?.rail_label || "PayTo";

  async function pay() {
    setState("paying");
    setErr(null);
    try {
      const r = await fetch(
        `${API}/api/c/matters/${policy_id}/pay/${envelope_id}`,
        { method: "POST", credentials: "include" }
      );
      if (!r.ok) {
        setErr(`${r.status}: ${await r.text()}`);
        setState("error");
        return;
      }
      const body = await r.json();
      setReceipt(body.payment);
      setState("paid");
      setTimeout(
        () => navigate(`/c/cover/${policy_id}/sign/${envelope_id}`),
        800
      );
    } catch (e: any) {
      setErr(String(e));
      setState("error");
    }
  }

  return (
    <section className="brokaid-tab" data-testid="client-pay-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Payment</p>
        <h1 className="brokaid-tab__title">Pay for the endorsement</h1>
        <p className="brokaid-tab__lede">
          Your broker has approved the change. Complete payment to move
          it to signing.
        </p>
      </header>

      <article
        className="brokaid-quote-card brokaid-pay-card"
        data-testid="pay-card"
      >
        <div className="brokaid-quote-card__head">
          <div>
            <p className="brokaid-quote-card__label">Rail</p>
            <h2 className="brokaid-quote-card__cover">{rail}</h2>
          </div>
          <MockedBadge label="payment" variant="client" />
        </div>

        {state === "idle" && (
          <>
            <p className="brokaid-quote-card__summary">
              This is a demo rail. Nothing real is charged. Clicking the
              button below will simulate a successful payment.
            </p>
            <div className="brokaid-quote-card__actions">
              <button
                type="button"
                className="brokaid-btn brokaid-btn--primary"
                onClick={pay}
                data-testid="pay-button"
              >
                Pay with {rail}
              </button>
            </div>
          </>
        )}

        {state === "paying" && (
          <p className="brokaid-quote-card__summary" data-testid="pay-working">
            Talking to the mocked rail…
          </p>
        )}

        {state === "paid" && receipt && (
          <div className="brokaid-pay-receipt" data-testid="pay-receipt">
            <p className="brokaid-quote-card__stat-label">Reference</p>
            <p className="brokaid-quote-card__stat-value">{receipt.ref}</p>
            <p className="brokaid-quote-card__stat-label">Status</p>
            <p className="brokaid-quote-card__stat-value">
              {receipt.status.toUpperCase()}
            </p>
            <p className="brokaid-quote-card__summary">
              Redirecting to signing…
            </p>
          </div>
        )}

        {state === "error" && (
          <p
            className="brokaid-quote-card__summary"
            data-testid="pay-error"
          >
            {err}
          </p>
        )}
      </article>
    </section>
  );
}

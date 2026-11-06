/**
 * ClientPay — /c/cover/:policy_id/pay/:envelope_id
 *
 * Payment Details: MOCKED BPAY + EFT remittance details and an
 * "I've paid this" action that records a single `payment.advised`
 * ledger event. Payments are advised, never processed.
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { fmtAud, fmtDate } from "../lib/format";
import { NextSteps } from "./client/Explain";

const API = import.meta.env.VITE_API_BASE || "";

interface Details {
  amount: string;
  currency: string;
  due_date: string;
  mocked?: boolean;
  crn?: string;
  bpay: { biller_code: string; reference: string } | null;
  eft: { account_name: string; bsb: string; account_number: string; reference: string } | null;
  message?: string;
}


function Row({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div className="brokaid-paydetails__row">
      <p className="brokaid-quote-card__stat-label">{label}</p>
      <p className="brokaid-quote-card__stat-value" data-testid={testId}>{value}</p>
    </div>
  );
}

export function ClientPay() {
  const { policy_id = "", envelope_id = "" } = useParams();
  const navigate = useNavigate();
  const [details, setDetails] = useState<Details | null>(null);
  const [state, setState] = useState<"loading" | "idle" | "sending" | "advised" | "error">("loading");
  const [err, setErr] = useState<string | null>(null);
  const base = `${API}/api/c/matters/${policy_id}`;

  useEffect(() => {
    (async () => {
      const r = await fetch(`${base}/pay/${envelope_id}`, { credentials: "include" });
      if (!r.ok) { setErr(`${r.status}: ${await r.text()}`); setState("error"); return; }
      const body = await r.json();
      setDetails(body.payment_details);
      setState(body.payment_advised ? "advised" : "idle");
    })().catch((e) => { setErr(String(e)); setState("error"); });
  }, [base, envelope_id]);

  async function advisePaid() {
    setState("sending");
    setErr(null);
    try {
      const r = await fetch(`${base}/paid/${envelope_id}`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (!r.ok) { setErr(`${r.status}: ${await r.text()}`); setState("error"); return; }
      setState("advised");
      setTimeout(() => navigate(`/c/cover/${policy_id}/sign/${envelope_id}`), 800);
    } catch (e: any) {
      setErr(String(e));
      setState("error");
    }
  }

  return (
    <section className="brokaid-tab" data-testid="client-pay-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Payment</p>
        <h1 className="brokaid-tab__title">Payment details</h1>
        <p className="brokaid-tab__lede">
          Pay by BPAY or bank transfer using the details below, then tell us
          you've paid. Your broker confirms receipt separately.
        </p>
      </header>
      <NextSteps flow="pay" />

      <article className="brokaid-quote-card brokaid-pay-card" data-testid="pay-card">
        <div className="brokaid-quote-card__head">
          <div>
            <p className="brokaid-quote-card__label">Amount due</p>
            <h2 className="brokaid-quote-card__cover" data-testid="pay-amount">
              {details ? fmtAud(details.amount) : "—"}
            </h2>
          </div>
          {details?.mocked !== false && <MockedBadge label="payment" variant="client" />}
        </div>

        {details && (
          <div className="brokaid-paydetails" data-testid="pay-details">
            <Row label="Due date" value={fmtDate(details.due_date)} testId="pay-due-date" />
            {details.crn ? <Row label="Your payment reference (CRN)" value={details.crn} testId="pay-crn" /> : null}
            {details.bpay ? (
              <>
                <h3 className="brokaid-paydetails__title">BPAY {details.mocked !== false && <span className="brokaid-paydetails__mocked">MOCKED</span>}</h3>
                <Row label="Biller code" value={details.bpay.biller_code} testId="pay-bpay-biller" />
                <Row label="Reference" value={details.bpay.reference} testId="pay-bpay-ref" />
              </>
            ) : null}
            {details.eft ? (
              <>
                <h3 className="brokaid-paydetails__title">Bank transfer (EFT) {details.mocked !== false && <span className="brokaid-paydetails__mocked">MOCKED</span>}</h3>
                <Row label="Account name" value={details.eft.account_name} testId="pay-eft-name" />
                <Row label="BSB" value={details.eft.bsb} testId="pay-eft-bsb" />
                <Row label="Account" value={details.eft.account_number} testId="pay-eft-account" />
                <Row label="Reference" value={details.eft.reference} testId="pay-eft-ref" />
              </>
            ) : <p data-testid="pay-details-pending">{details.message || "Your broker will send payment details"}</p>}
          </div>
        )}

        {state === "idle" && (
          <div className="brokaid-quote-card__actions">
            <button type="button" className="brokaid-btn brokaid-btn--primary"
              onClick={advisePaid} data-testid="paid-button">
              I've paid this
            </button>
          </div>
        )}
        {state === "sending" && (
          <p className="brokaid-quote-card__summary" data-testid="paid-sending">Recording…</p>
        )}
        {state === "advised" && (
          <div className="brokaid-pay-receipt" data-testid="paid-confirmation">
            <p className="brokaid-quote-card__summary">
              Thanks — we've noted you've paid. Your broker will confirm once it arrives.
            </p>
            <button type="button" className="brokaid-btn brokaid-btn--secondary"
              onClick={() => navigate(`/c/cover/${policy_id}/sign/${envelope_id}`)}
              data-testid="paid-continue-sign">
              Continue to accept
            </button>
          </div>
        )}
        {state === "error" && (
          <p className="brokaid-quote-card__summary" data-testid="pay-error">{err}</p>
        )}
      </article>
    </section>
  );
}

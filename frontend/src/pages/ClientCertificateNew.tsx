/**
 * ClientCertificateNew — "Get a certificate" flow.
 *
 * Route: /c/cover/:policy_id/certificate/new
 *
 * Steps
 *   1  Holder      · name + address + optional email
 *   2  Requirements· interest_type + effective dates + special text
 *   3  Preview     · confirm before issuing
 *   4  Issued      · shows QR (verify deep-link) + Download PDF
 *
 * Meridian light expression via existing Harbour tokens. No agent
 * internal names on screen — user-visible labels only ("Certificate",
 * "Placement", "Broker's assistant").
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { fmtDate } from "../lib/format";

const API = import.meta.env.VITE_API_BASE || "";

type InterestType = "additional_insured" | "interested_party" | "loss_payee";

type IssuedCert = {
  certificate_id: string;
  holder_display: string;
  holder: { name: string; address: string; email: string };
  requirements: {
    interest_type: string;
    effective_from: string;
    effective_to: string;
    special_requirements: string;
  };
  insurer_display: string;
  product: string;
  cover_types: any[];
  effective_from: string;
  effective_to: string;
  issued_at: string;
};

const INTEREST_LABEL: Record<InterestType, string> = {
  additional_insured: "Additional insured",
  interested_party: "Interested party",
  loss_payee: "Loss payee",
};

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}
function plusYearISO(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() + 1);
  return d.toISOString().slice(0, 10);
}

export function ClientCertificateNew() {
  const { policy_id = "" } = useParams();
  const navigate = useNavigate();

  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [holderName, setHolderName] = useState("");
  const [holderAddress, setHolderAddress] = useState("");
  const [holderEmail, setHolderEmail] = useState("");

  const [interestType, setInterestType] =
    useState<InterestType>("additional_insured");
  const [effectiveFrom, setEffectiveFrom] = useState(todayISO());
  const [effectiveTo, setEffectiveTo] = useState(plusYearISO());
  const [special, setSpecial] = useState("");

  const [issued, setIssued] = useState<IssuedCert | null>(null);
  const [rawToken, setRawToken] = useState<string | null>(null);
  const [ledgerEventId, setLedgerEventId] = useState<string | null>(null);

  const verifyUrl = useMemo(
    () =>
      rawToken
        ? `${window.location.origin}/verify/${rawToken}`
        : "",
    [rawToken]
  );

  const canContinue1 = holderName.trim().length > 1;
  const canContinue2 = effectiveFrom && effectiveTo;

  async function issue() {
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/c/certificate/regen`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          policy_id,
          holder: {
            name: holderName.trim(),
            address: holderAddress.trim(),
            email: holderEmail.trim(),
          },
          requirements: {
            interest_type: interestType,
            effective_from: effectiveFrom,
            effective_to: effectiveTo,
            special_requirements: special.trim(),
          },
        }),
      });
      if (!r.ok) {
        setError(`Issue failed (HTTP ${r.status})`);
        setSubmitting(false);
        return;
      }
      const body = await r.json();
      setIssued(body.certificate as IssuedCert);
      setRawToken(body.raw_token as string);
      setLedgerEventId(body.ledger_event_id ?? null);
      setStep(4);
    } catch (e) {
      setError("Network error");
    } finally {
      setSubmitting(false);
    }
  }

  async function downloadPdf() {
    if (!issued || !rawToken) return;
    const r = await fetch(
      `${API}/api/c/certificate/${issued.certificate_id}/pdf`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          raw_token: rawToken,
          verify_origin: window.location.origin,
        }),
      }
    );
    if (!r.ok) {
      setError(`PDF failed (HTTP ${r.status})`);
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `certificate-${issued.certificate_id.slice(0, 8)}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  const stepPill = (n: number, label: string) => (
    <span
      className={`brokaid-cert-steps__pill${
        step === n ? " is-active" : step > n ? " is-done" : ""
      }`}
      data-testid={`cert-new-step-${n}`}
    >
      <span className="brokaid-cert-steps__num">{n}</span>
      {label}
    </span>
  );

  return (
    <section className="brokaid-tab" data-testid="cert-new-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Certificate</p>
        <h1 className="brokaid-tab__title">Get a certificate</h1>
        <p className="brokaid-tab__lede">
          Issue a certificate of currency for a third party — a landlord,
          panel or contract partner. Ready in under two minutes.
        </p>
      </header>

      <nav className="brokaid-cert-steps" aria-label="Progress">
        {stepPill(1, "Holder")}
        {stepPill(2, "Requirements")}
        {stepPill(3, "Preview")}
        {stepPill(4, "Issued")}
      </nav>

      {step === 1 && (
        <form
          className="brokaid-cert-form"
          data-testid="cert-new-step-1-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (canContinue1) setStep(2);
          }}
        >
          <label className="brokaid-form__label">
            Holder name
            <input
              value={holderName}
              onChange={(e) => setHolderName(e.target.value)}
              placeholder="e.g. Sydney Harbour Marina Pty Ltd"
              maxLength={200}
              required
              data-testid="cert-new-holder-name"
              autoFocus
            />
          </label>
          <label className="brokaid-form__label">
            Address
            <input
              value={holderAddress}
              onChange={(e) => setHolderAddress(e.target.value)}
              placeholder="Street, suburb, state, postcode"
              maxLength={400}
              data-testid="cert-new-holder-address"
            />
          </label>
          <label className="brokaid-form__label">
            Email (optional)
            <input
              type="email"
              value={holderEmail}
              onChange={(e) => setHolderEmail(e.target.value)}
              placeholder="ops@example.com"
              maxLength={200}
              data-testid="cert-new-holder-email"
            />
          </label>
          <div className="brokaid-cert-form__actions">
            <button
              type="button"
              className="brokaid-btn brokaid-btn--secondary"
              onClick={() => navigate("/c/cover")}
              data-testid="cert-new-cancel"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="brokaid-btn brokaid-btn--primary"
              disabled={!canContinue1}
              data-testid="cert-new-continue-1"
            >
              Continue
            </button>
          </div>
        </form>
      )}

      {step === 2 && (
        <form
          className="brokaid-cert-form"
          data-testid="cert-new-step-2-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (canContinue2) setStep(3);
          }}
        >
          <fieldset className="brokaid-cert-form__fieldset">
            <legend className="brokaid-form__label">Interest type</legend>
            {(Object.keys(INTEREST_LABEL) as InterestType[]).map((k) => (
              <label
                key={k}
                className={`brokaid-cert-radio${
                  interestType === k ? " is-selected" : ""
                }`}
                data-testid={`cert-new-interest-${k}`}
              >
                <input
                  type="radio"
                  name="interest-type"
                  value={k}
                  checked={interestType === k}
                  onChange={() => setInterestType(k)}
                />
                <span>{INTEREST_LABEL[k]}</span>
              </label>
            ))}
          </fieldset>
          <div className="brokaid-cert-form__row">
            <label className="brokaid-form__label">
              Effective from
              <input
                type="date"
                value={effectiveFrom}
                onChange={(e) => setEffectiveFrom(e.target.value)}
                required
                data-testid="cert-new-effective-from"
              />
            </label>
            <label className="brokaid-form__label">
              Effective to
              <input
                type="date"
                value={effectiveTo}
                onChange={(e) => setEffectiveTo(e.target.value)}
                required
                data-testid="cert-new-effective-to"
              />
            </label>
          </div>
          <label className="brokaid-form__label">
            Special requirements
            <textarea
              value={special}
              onChange={(e) => setSpecial(e.target.value)}
              placeholder="e.g. Additional insured on Public Liability for wet-berth operations."
              rows={4}
              maxLength={2000}
              data-testid="cert-new-special"
            />
          </label>
          <div className="brokaid-cert-form__actions">
            <button
              type="button"
              className="brokaid-btn brokaid-btn--secondary"
              onClick={() => setStep(1)}
              data-testid="cert-new-back-2"
            >
              Back
            </button>
            <button
              type="submit"
              className="brokaid-btn brokaid-btn--primary"
              disabled={!canContinue2}
              data-testid="cert-new-continue-2"
            >
              Continue
            </button>
          </div>
        </form>
      )}

      {step === 3 && (
        <article
          className="brokaid-cert-preview"
          data-testid="cert-new-step-3-preview"
        >
          <h2 className="brokaid-cert-preview__title">Preview</h2>
          <dl className="brokaid-cert-preview__grid">
            <dt>Holder</dt>
            <dd data-testid="cert-new-preview-holder">{holderName}</dd>
            {holderAddress && (
              <>
                <dt>Address</dt>
                <dd>{holderAddress}</dd>
              </>
            )}
            {holderEmail && (
              <>
                <dt>Email</dt>
                <dd>{holderEmail}</dd>
              </>
            )}
            <dt>Interest</dt>
            <dd>{INTEREST_LABEL[interestType]}</dd>
            <dt>Effective</dt>
            <dd>
              {effectiveFrom} — {effectiveTo}
            </dd>
            {special && (
              <>
                <dt>Special</dt>
                <dd>{special}</dd>
              </>
            )}
          </dl>
          {error && (
            <p className="brokaid-form__err" data-testid="cert-new-error">
              {error}
            </p>
          )}
          <div className="brokaid-cert-form__actions">
            <button
              type="button"
              className="brokaid-btn brokaid-btn--secondary"
              onClick={() => setStep(2)}
              disabled={submitting}
              data-testid="cert-new-back-3"
            >
              Back
            </button>
            <button
              type="button"
              className="brokaid-btn brokaid-btn--primary"
              onClick={issue}
              disabled={submitting}
              data-testid="cert-new-issue"
            >
              {submitting ? "Issuing…" : "Issue certificate"}
            </button>
          </div>
        </article>
      )}

      {step === 4 && issued && (
        <article className="brokaid-cert-issued" data-testid="cert-new-step-4-issued">
          <div className="brokaid-cert-issued__banner">
            <span className="brokaid-cert-issued__tick" aria-hidden="true">
              ✓
            </span>
            <div>
              <p className="brokaid-cert-issued__title">Certificate issued</p>
              <p className="brokaid-cert-issued__sub">
                Share the verifier link with the holder. It works from any
                phone or browser.
              </p>
            </div>
          </div>

          <div className="brokaid-cert-issued__panel">
            <div className="brokaid-cert-issued__panel-head">
              <p className="brokaid-quote-card__stat-label">Insurer</p>
              <h2
                className="brokaid-quote-card__cover"
                data-testid="cert-new-insurer"
              >
                {issued.insurer_display}
              </h2>
              <MockedBadge label="carrier" variant="client" />
            </div>
            <dl className="brokaid-cert-preview__grid">
              <dt>Holder</dt>
              <dd data-testid="cert-new-issued-holder">
                {issued.holder_display}
              </dd>
              <dt>Product</dt>
              <dd>{issued.product}</dd>
              <dt>Effective</dt>
              <dd>
                {fmtDate(issued.effective_from)} —{" "}
                {fmtDate(issued.effective_to)}
              </dd>
              {ledgerEventId && (
                <>
                  <dt>Ledger</dt>
                  <dd data-testid="cert-new-ledger-event">
                    <code>{ledgerEventId.slice(0, 12)}…</code>
                  </dd>
                </>
              )}
            </dl>
          </div>

          <div className="brokaid-cert-issued__qr-wrap">
            {rawToken && (
              <img
                className="brokaid-cert-issued__qr"
                src={`${API}/api/c/certificate/${issued.certificate_id}/qr.png?t=${encodeURIComponent(
                  rawToken
                )}&o=${encodeURIComponent(window.location.origin)}`}
                alt="Scan to verify"
                width={200}
                height={200}
                data-testid="cert-new-qr"
              />
            )}
            <div className="brokaid-cert-issued__qr-copy">
              <p className="brokaid-cert-issued__label">Verifier link</p>
              <p
                className="brokaid-cert-issued__url"
                data-testid="cert-new-verify-url"
              >
                {verifyUrl}
              </p>
              <button
                type="button"
                className="brokaid-btn brokaid-btn--secondary"
                onClick={() => navigator.clipboard.writeText(verifyUrl)}
                data-testid="cert-new-copy-url"
              >
                Copy link
              </button>
            </div>
          </div>

          <div className="brokaid-cert-form__actions">
            <button
              type="button"
              className="brokaid-btn brokaid-btn--secondary"
              onClick={() => navigate("/c/cover")}
              data-testid="cert-new-done"
            >
              Done
            </button>
            <button
              type="button"
              className="brokaid-btn brokaid-btn--primary"
              onClick={downloadPdf}
              data-testid="cert-new-download-pdf"
            >
              Download PDF
            </button>
          </div>
          {error && (
            <p className="brokaid-form__err">{error}</p>
          )}
        </article>
      )}
    </section>
  );
}

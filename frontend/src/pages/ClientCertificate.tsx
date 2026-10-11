/**
 * ClientCertificate — /c/cover/:policy_id/certificate/:envelope_id
 *
 * Displays the freshly regenerated certificate with a QR code linking
 * to the public /verify/{token} route. MOCKED · CARRIER pill on the
 * insurer field. Includes "Copy verifier link" and a print button.
 */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { fmtDate } from "../lib/format";

const API = import.meta.env.VITE_API_BASE || "";

type Cert = {
  certificate_id: string;
  policy_id: string;
  holder_display: string;
  insurer_display: string;
  product: string;
  cover_types: any[];
  effective_from: string;
  effective_to: string;
  issued_at: string;
  mocked_insurer: true;
};

/** Tiny inline SVG QR renderer using the browser's built-in canvas + a
 *  minimal fetch to `qrserver.com` is NOT allowed (offline demo). We use
 *  a data-URL for the verifier link and rely on the `qrcode-svg` inline
 *  encoder to draw an ASCII/SVG fallback. For the demo, we render a
 *  data-URI QR from a small pure-JS encoder embedded here.
 *
 *  For pragmatism, we don't ship a QR encoder yet — we render the token
 *  as a monospaced code + a large-print link. Phase 16 hardening can
 *  swap in a real SVG QR encoder.
 */
function VerifierBlock({
  url, certificateId,
}: {
  url: string;
  certificateId: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const qrSrc = certificateId
    ? `${API}/api/c/certificate/${certificateId}/qr.png`
    : "";
  return (
    <div className="brokaid-cert__verifier" data-testid="cert-verifier-block">
      <p className="brokaid-quote-card__stat-label">Public verifier</p>
      <div
        className="brokaid-cert__qr"
        data-testid="cert-qr"
        aria-label="QR code for the verifier link"
      >
        {qrSrc ? (
          <img
            src={qrSrc}
            width={140}
            height={140}
            alt="Certificate QR"
            data-testid="cert-qr-img"
          />
        ) : (
          <p className="brokaid-quote-card__summary">QR pending…</p>
        )}
      </div>
      <p
        className="brokaid-cert__url"
        data-testid="cert-verifier-url"
      >
        {url}
      </p>
      <button
        type="button"
        className="brokaid-btn brokaid-btn--secondary"
        onClick={async () => {
          await navigator.clipboard.writeText(url);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        data-testid="cert-copy-link"
      >
        {copied ? "Copied" : "Copy verifier link"}
      </button>
    </div>
  );
}

export function ClientCertificate() {
  const { policy_id = "" } = useParams();
  const [cert, setCert] = useState<Cert | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const r = await fetch(
        `${API}/api/c/matters/${policy_id}/status`,
        { credentials: "include" }
      );
      if (!r.ok) {
        setErr(`${r.status}`);
        return;
      }
      const body = await r.json();
      if (body.certificate) setCert(body.certificate as Cert);
      // Token needs to come from the finalise response — we cache it
      // on window when arriving via the flow.
      const raw = window.sessionStorage.getItem(`vfy:${policy_id}`);
      if (raw) setToken(raw);
    })();
  }, [policy_id]);

  if (err) {
    return (
      <section className="brokaid-tab" data-testid="cert-error">
        <p className="brokaid-quote-card__summary">
          Certificate not available: {err}
        </p>
      </section>
    );
  }
  if (!cert) {
    return (
      <section className="brokaid-tab" data-testid="cert-loading">
        <p className="brokaid-quote-card__summary">Loading certificate…</p>
      </section>
    );
  }

  const origin =
    typeof window !== "undefined" ? window.location.origin : "";
  const verifierUrl = token ? `${origin}/verify/${token}` : "(token unavailable)";

  return (
    <section className="brokaid-tab" data-testid="client-cert-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Certificate</p>
        <h1 className="brokaid-tab__title">Certificate of currency</h1>
        <p className="brokaid-tab__lede">
          Regenerated after your endorsement. Share the verifier link
          with anyone who needs to check your cover.
        </p>
      </header>

      <article className="brokaid-cert" data-testid="cert-card">
        <div className="brokaid-quote-card__head">
          <div>
            <p className="brokaid-quote-card__label">Insurer</p>
            <h2
              className="brokaid-quote-card__cover"
              data-testid="cert-insurer"
            >
              {cert.insurer_display}
            </h2>
          </div>
          <MockedBadge label="carrier" variant="client" />
        </div>

        <div className="brokaid-quote-card__grid">
          <div>
            <p className="brokaid-quote-card__stat-label">Holder</p>
            <p
              className="brokaid-quote-card__stat-value"
              data-testid="cert-holder"
            >
              {cert.holder_display}
            </p>
          </div>
          <div>
            <p className="brokaid-quote-card__stat-label">Product</p>
            <p className="brokaid-quote-card__stat-value">{cert.product}</p>
          </div>
          <div>
            <p className="brokaid-quote-card__stat-label">Effective</p>
            <p className="brokaid-quote-card__stat-value">
              {fmtDate(cert.effective_from)} —{" "}
              {fmtDate(cert.effective_to)}
            </p>
          </div>
        </div>

        {cert.cover_types.length > 0 && (
          <div
            className="brokaid-quote-card__terms"
            data-testid="cert-cover-types"
          >
            <p className="brokaid-quote-card__stat-label">Cover</p>
            <ul className="brokaid-quote-card__conditions">
              {cert.cover_types.map((c: any, i: number) => (
                <li key={c?.type ?? i}>
                  {c.type} — limit any one {c.limit_any_one} {c.currency}
                </li>
              ))}
            </ul>
          </div>
        )}

        <VerifierBlock url={verifierUrl} certificateId={cert.certificate_id} />

        <div className="brokaid-quote-card__actions">
          <button
            type="button"
            className="brokaid-btn brokaid-btn--secondary"
            onClick={() => window.print()}
            data-testid="cert-print-button"
          >
            Download (print to PDF)
          </button>
        </div>
      </article>
    </section>
  );
}

/**
 * VerifyPublic — /verify, /verify/:token, /verify?token=...
 *
 * PUBLIC, unauthenticated. Meridian light. Supports:
 *   · path parameter (`/verify/<token>`)  — legacy + primary
 *   · query parameter (`/verify?token=<token>`) — QR deep-link
 *   · no token (bare `/verify`) — verifier pastes the token
 *
 * Verdicts (returned in body + inferred from HTTP status for BC):
 *   VALID     · green banner + certificate card
 *   TAMPERED  · red banner + do-not-accept messaging
 *   EXPIRED   · amber banner
 *   REVOKED   · amber banner
 *   NOT_FOUND · neutral banner
 */
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { fmtAud, fmtDate, fmtDateTime } from "../lib/format";

const API = import.meta.env.VITE_API_BASE || "";

type Verdict =
  | "VALID"
  | "TAMPERED"
  | "EXPIRED"
  | "REVOKED"
  | "NOT_FOUND"
  | "RATE_LIMITED"
  | "ERROR";

type Payload = {
  verdict: Verdict;
  holder_display: string;
  holder: { name?: string; address?: string; email?: string };
  requirements: {
    interest_type?: string;
    effective_from?: string;
    effective_to?: string;
    special_requirements?: string;
  };
  insurer_display: string;
  product: string;
  cover_types: any[];
  effective_from: string;
  effective_to: string;
  issued_at: string;
  policy_id: string;
  certificate_id: string;
  ledger_event_id: string | null;
  mocked_insurer: true;
};

const INTEREST_LABEL: Record<string, string> = {
  additional_insured: "Additional insured",
  interested_party: "Interested party",
  loss_payee: "Loss payee",
};

function verdictFromStatus(status: number, body: any): Verdict {
  if (body && typeof body === "object") {
    const v = (body.verdict as string) || (body.detail?.verdict as string);
    if (v) return v as Verdict;
  }
  if (status === 200) return "VALID";
  if (status === 404) return "NOT_FOUND";
  if (status === 410) return "EXPIRED";
  if (status === 429) return "RATE_LIMITED";
  return "ERROR";
}

async function verifyToken(token: string): Promise<{
  verdict: Verdict;
  cert: Payload | null;
}> {
  try {
    const r = await fetch(`${API}/api/verifier/${encodeURIComponent(token)}`);
    let body: any = null;
    try {
      body = await r.json();
    } catch {}
    const verdict = verdictFromStatus(r.status, body);
    if (verdict === "VALID" || verdict === "TAMPERED") {
      return { verdict, cert: body as Payload };
    }
    return { verdict, cert: null };
  } catch {
    return { verdict: "ERROR", cert: null };
  }
}

export function VerifyPublic() {
  const { token: pathToken = "" } = useParams();
  const [search] = useSearchParams();
  const queryToken = search.get("token") || "";
  const startingToken = pathToken || queryToken;

  const [pastedToken, setPastedToken] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "done">(
    startingToken ? "loading" : "idle"
  );
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [cert, setCert] = useState<Payload | null>(null);

  useEffect(() => {
    if (!startingToken) return;
    (async () => {
      const res = await verifyToken(startingToken);
      setVerdict(res.verdict);
      setCert(res.cert);
      setState("done");
    })();
  }, [startingToken]);

  async function submitPasted(e: React.FormEvent) {
    e.preventDefault();
    const t = pastedToken.trim();
    if (t.length < 20) {
      setVerdict("NOT_FOUND");
      setState("done");
      return;
    }
    setState("loading");
    const res = await verifyToken(t);
    setVerdict(res.verdict);
    setCert(res.cert);
    setState("done");
  }

  return (
    <main
      className="surface-harbour brokaid-verify"
      data-testid="verify-root"
    >
      <header className="brokaid-verify__head">
        <p className="brokaid-verify__mark">
          brok<em>AI</em>d
        </p>
        <h1 className="brokaid-verify__title" data-testid="verify-title">
          Certificate verification
        </h1>
        <p className="brokaid-verify__lede">
          Scan a QR from a brokAId certificate or paste its token below.
        </p>
      </header>

      {state === "idle" && (
        <form
          className="brokaid-verify__lookup"
          onSubmit={submitPasted}
          data-testid="verify-lookup-form"
        >
          <label className="brokaid-form__label">
            Verifier token
            <input
              value={pastedToken}
              onChange={(e) => setPastedToken(e.target.value)}
              placeholder="Paste the token from the certificate or QR"
              data-testid="verify-token-input"
              spellCheck={false}
              autoFocus
            />
          </label>
          <button
            type="submit"
            className="brokaid-btn brokaid-btn--primary"
            data-testid="verify-token-submit"
          >
            Verify
          </button>
        </form>
      )}

      {state === "loading" && (
        <p className="brokaid-verify__body" data-testid="verify-loading">
          Checking…
        </p>
      )}

      {state === "done" && verdict && (
        <>
          {verdict === "VALID" && cert && <ValidCard cert={cert} />}
          {verdict === "TAMPERED" && cert && <TamperedCard cert={cert} />}
          {verdict === "EXPIRED" && <BannerOnly kind="expired" />}
          {verdict === "REVOKED" && <BannerOnly kind="revoked" />}
          {verdict === "NOT_FOUND" && <BannerOnly kind="not_found" />}
          {verdict === "RATE_LIMITED" && <BannerOnly kind="rate_limited" />}
          {verdict === "ERROR" && <BannerOnly kind="error" />}
          <button
            type="button"
            className="brokaid-btn brokaid-btn--secondary brokaid-verify__reset"
            onClick={() => {
              setState("idle");
              setVerdict(null);
              setCert(null);
              setPastedToken("");
              // Clear url params to allow another lookup
              window.history.replaceState(null, "", "/verify");
            }}
            data-testid="verify-check-another"
          >
            Check another
          </button>
        </>
      )}
    </main>
  );
}

function ValidCard({ cert }: { cert: Payload }) {
  return (
    <article className="brokaid-verify__card" data-testid="verify-card">
      <div
        className="brokaid-verify__banner brokaid-verify__banner--valid"
        data-testid="verify-banner-valid"
      >
        <span className="brokaid-verify__banner-tick" aria-hidden="true">
          ✓
        </span>
        <div>
          <p className="brokaid-verify__banner-title">Certificate verified</p>
          <p className="brokaid-verify__banner-sub">
            This certificate is current and issued by Harbourline Insurance Brokers
            (fictional).
          </p>
        </div>
      </div>

      <CertBody cert={cert} />
    </article>
  );
}

function TamperedCard({ cert }: { cert: Payload }) {
  return (
    <article className="brokaid-verify__card" data-testid="verify-card">
      <div
        className="brokaid-verify__banner brokaid-verify__banner--tampered"
        data-testid="verify-banner-tampered"
      >
        <span className="brokaid-verify__banner-tick" aria-hidden="true">
          !
        </span>
        <div>
          <p className="brokaid-verify__banner-title">
            Certificate tampered — do not accept
          </p>
          <p className="brokaid-verify__banner-sub">
            The payload signature does not match. Ask the holder to request a
            fresh certificate from their broker.
          </p>
        </div>
      </div>

      <CertBody cert={cert} muted />
    </article>
  );
}

function CertBody({ cert, muted }: { cert: Payload; muted?: boolean }) {
  const interestLabel =
    INTEREST_LABEL[cert.requirements?.interest_type || ""] || "—";
  return (
    <div
      className={
        "brokaid-verify__body-wrap" + (muted ? " is-muted" : "")
      }
    >
      <div className="brokaid-verify__row">
        <div>
          <p className="brokaid-verify__label">Insurer</p>
          <p
            className="brokaid-verify__value"
            data-testid="verify-insurer"
          >
            {cert.insurer_display}
          </p>
        </div>
      </div>

      <div className="brokaid-verify__grid">
        <div>
          <p className="brokaid-verify__label">Holder</p>
          <p className="brokaid-verify__value" data-testid="verify-holder">
            {cert.holder_display}
          </p>
          {cert.holder?.address && (
            <p className="brokaid-verify__meta">{cert.holder.address}</p>
          )}
        </div>
        <div>
          <p className="brokaid-verify__label">Product</p>
          <p className="brokaid-verify__value">{cert.product}</p>
        </div>
        <div>
          <p className="brokaid-verify__label">Interest</p>
          <p className="brokaid-verify__value">{interestLabel}</p>
        </div>
        <div>
          <p className="brokaid-verify__label">Effective</p>
          <p className="brokaid-verify__value">
            {fmtDate(cert.effective_from)} — {fmtDate(cert.effective_to)}
          </p>
        </div>
      </div>

      {cert.cover_types?.length > 0 && (
        <div className="brokaid-verify__cover-list">
          <p className="brokaid-verify__label">Cover</p>
          <ul>
            {cert.cover_types.map((c: any, i: number) => (
              <li key={c?.type ?? i}>
                {c.type} — limit any one {fmtAud(c.limit_any_one)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {cert.vehicles?.length > 0 && (
        <div className="brokaid-verify__cover-list" data-testid="verify-vehicles">
          <p className="brokaid-verify__label">Vehicles</p>
          <ul>
            {cert.vehicles.map((v: any, i: number) => (
              <li key={v?.rego ?? i} data-testid={`verify-vehicle-${i}`}>
                {v.rego} · {[v.year, v.make, v.model].filter(Boolean).join(" ")}
                {v.driver_assigned ? ` · primary driver ${v.driver_assigned}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {cert.requirements?.special_requirements && (
        <div>
          <p className="brokaid-verify__label">Special requirements</p>
          <p className="brokaid-verify__value">
            {cert.requirements.special_requirements}
          </p>
        </div>
      )}

      <p className="brokaid-verify__footnote">
        Issued {fmtDateTime(cert.issued_at)}
        {cert.ledger_event_id ? (
          <>
            {" · "}
            <span data-testid="verify-ledger-event">
              ledger {cert.ledger_event_id.slice(0, 12)}…
            </span>
          </>
        ) : null}
      </p>
    </div>
  );
}

function BannerOnly({
  kind,
}: {
  kind: "expired" | "revoked" | "not_found" | "rate_limited" | "error";
}) {
  const config: Record<typeof kind, {
    testid: string;
    title: string;
    sub: string;
    modifier: string;
  }> = {
    expired: {
      testid: "verify-expired",
      title: "Certificate expired",
      sub: "This certificate link is past its effective date. Ask the holder to reissue.",
      modifier: "brokaid-verify__banner--warn",
    },
    revoked: {
      testid: "verify-revoked",
      title: "Certificate revoked",
      sub: "The broker has withdrawn this certificate. Do not accept it.",
      modifier: "brokaid-verify__banner--warn",
    },
    not_found: {
      testid: "verify-not-found",
      title: "Certificate not found",
      sub: "We couldn't find a certificate for that link. Check the token and try again.",
      modifier: "brokaid-verify__banner--muted",
    },
    rate_limited: {
      testid: "verify-rate-limited",
      title: "Too many checks",
      sub: "You've checked too many links recently. Try again shortly.",
      modifier: "brokaid-verify__banner--muted",
    },
    error: {
      testid: "verify-error",
      title: "Something went wrong",
      sub: "Please try again in a moment.",
      modifier: "brokaid-verify__banner--muted",
    },
  } as const;
  const cfg = config[kind];
  return (
    <div
      className={`brokaid-verify__banner ${cfg.modifier}`}
      data-testid={cfg.testid}
    >
      <span className="brokaid-verify__banner-tick" aria-hidden="true">
        !
      </span>
      <div>
        <p className="brokaid-verify__banner-title">{cfg.title}</p>
        <p className="brokaid-verify__banner-sub">{cfg.sub}</p>
      </div>
    </div>
  );
}

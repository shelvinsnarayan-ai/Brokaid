/**
 * /login — persona picker (primary) + secondary "I already have an
 * account" link to /o/login.  Uses AUTH_DEV_MODE impersonation on the
 * deployed backend for one-click demo access.  If the backend flips
 * AUTH_DEV_MODE off the impersonate endpoint returns 4xx/5xx; we
 * catch it and render an inline card-level fallback instead of
 * crashing.
 *
 * Persona map (emails verified against seed_users.py in R7-Landing):
 *   ada   -> principal@burmac.example  -> /o   (operator seat)
 *   cass  -> ae@burmac.example         -> /o   (executive seat)
 *   kai   -> owner@fictionalclient.example -> /c (client seat)
 *
 * Query param ?persona=<slug> is a visual pre-select only. No
 * auto-submit — the SIRC judge still clicks the card. This avoids
 * the footgun of a bookmarked/shared URL triggering an impersonation
 * without the user seeing what they're doing.
 */
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { BrandMark } from "../components/BrandMark";
import { BrandWordmark } from "../components/BrandWordmark";

const API = import.meta.env.VITE_API_BASE || "";

interface Persona {
  slug: "ada" | "cass" | "kai";
  name: string;
  seat: string;
  body: string;
  email: string;
  route: "/o" | "/c";
}

const PERSONAS: Persona[] = [
  {
    slug: "ada",
    name: "Ada Principal",
    seat: "Operator seat",
    body: "Sees the whole queue, drives compliance and endorsement scenes.",
    email: "principal@burmac.example",
    route: "/o",
  },
  {
    slug: "cass",
    name: "Cass Executive",
    seat: "Placement seat",
    body: "AE workflow: quote → approve → issue.",
    email: "ae@burmac.example",
    route: "/o",
  },
  {
    slug: "kai",
    name: "Kai Owner",
    seat: "Client seat",
    body: "Requests a change, pays, signs, sees the certificate.",
    email: "owner@fictionalclient.example",
    route: "/c",
  },
];

export function Login() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const preselect = useMemo(() => {
    const p = params.get("persona");
    return PERSONAS.some((x) => x.slug === p) ? p : null;
  }, [params]);

  const signIn = async (p: Persona) => {
    setBusy(p.slug);
    setError(null);
    try {
      const r = await fetch(`${API}/api/auth/dev/impersonate`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: p.email }),
      });
      if (!r.ok) {
        setError(
          "Please contact demo@synaion.com.au for demo access."
        );
        setBusy(null);
        return;
      }
      nav(p.route);
    } catch {
      setError(
        "Please contact demo@synaion.com.au for demo access."
      );
      setBusy(null);
    }
  };

  return (
    <main className="surface-harbour brokaid-login" data-testid="login-root">
      <nav className="brokaid-landing__nav" aria-label="Primary">
        <div className="brokaid-landing__nav-mark" aria-label="brokAId">
          <BrandMark variant="pulse-colour" size={28} alt="" />
          <BrandWordmark variant="harbour-bold" height={20} alt="brokAId" />
        </div>
        <span
          className="brokaid-demo-pill"
          title="This deployment permits dev-mode persona impersonation for SIRC demo purposes."
          data-testid="demo-mode-pill"
        >
          Demo mode
        </span>
      </nav>

      <section className="brokaid-login__hero">
        <h1 className="brokaid-landing__title brokaid-login__title">
          Try brokAId as…
        </h1>
        <p className="brokaid-landing__lede">
          One-click into a seeded persona. Every session is fictional; every
          external call (payment, e-sign, insurer, carrier) is mocked and
          labelled on screen.
        </p>
      </section>

      <ul
        className="brokaid-login__personas"
        data-testid="login-personas"
        aria-live="polite"
      >
        {PERSONAS.map((p) => {
          const isBusy = busy === p.slug;
          const isPreselect = preselect === p.slug;
          const cls = [
            "brokaid-login__persona",
            isPreselect ? "is-preselect" : "",
            isBusy ? "is-busy" : "",
          ].filter(Boolean).join(" ");
          return (
            <li key={p.slug} className="brokaid-landing__persona-wrap">
              <button
                type="button"
                className={cls}
                onClick={() => void signIn(p)}
                disabled={busy !== null}
                aria-label={`Sign in as ${p.name}, ${p.seat}`}
                data-testid={`login-persona-${p.slug}`}
              >
                <span className="brokaid-landing__persona-seat">{p.seat}</span>
                <span className="brokaid-landing__persona-name">{p.name}</span>
                <span className="brokaid-landing__persona-body">{p.body}</span>
                <span className="brokaid-login__persona-cta">
                  {isBusy ? "Signing in…" : `Sign in as ${p.name.split(" ")[0]}`}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {error ? (
        <div
          className="brokaid-login__error"
          data-testid="login-error"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      <div className="brokaid-login__secondary">
        <Link
          to="/o/login"
          className="brokaid-login__existing"
          data-testid="login-existing-account"
        >
          I already have an account →
        </Link>
      </div>

      <footer className="brokaid-landing__foot">
        <span>© 2026 Synaion Pty Ltd</span>
        <span aria-hidden="true">·</span>
        <span>SIRC 2026</span>
        <span aria-hidden="true">·</span>
        <Link
          to="/verify"
          className="brokaid-landing__foot-link"
          data-testid="login-foot-verify"
        >
          Verify a ledger export
        </Link>
      </footer>
    </main>
  );
}

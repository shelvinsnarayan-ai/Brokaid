import { Link } from "react-router-dom";
import { BrandMark } from "../components/BrandMark";
import { BrandWordmark } from "../components/BrandWordmark";

/**
 * Landing — mixed-audience marketing surface for the deployed demo.
 *
 * Structure (top → bottom):
 *   1. Top nav        — brand lockup + "Log in" link
 *   2. Hero           — H1 tagline + value-prop lede + primary CTA
 *   3. What is brokAId? — one plain-English paragraph
 *   4. How it works   — 4 scene cards drawn from DEMO_RUNBOOK.md scenes 1/3/4/7
 *   5. Try it as…     — 3 persona cards → /login pre-filled per persona
 *   6. Footer         — Synaion + SIRC 2026 + ledger-verifier link
 *
 * Contrast discipline (WCAG AA at intended thresholds; palette per
 * packages/brand-tokens v1.0 canon, post R7-Light-Tokens snap):
 *   • H1, section H2      — --harbour on --sand           10.53:1 (thr 4.5)
 *   • body <p>            — --slate-strong on --sand       8.13:1 (thr 4.5)
 *   • primary CTA         — #fff on --sea-strong           5.88:1 (thr 4.5)
 *   • demo-mode pill      — #fff on --sea-strong           5.88:1 (thr 4.5)
 *   • Log in nav pill     — --harbour on --sand           10.53:1 (thr 4.5)
 *   • scene / persona n   — --sea-strong on --sand         5.13:1 (thr 4.5,
 *                            mono 12px so must clear body — NOT coral,
 *                            which would trip the coral-body guard)
 *   • kbd hint            — --sea-strong on --sand         5.13:1 (thr 4.5)
 *   • footer body         — --slate-strong on --sand       8.13:1 (thr 4.5)
 *   • footer link         — --sea-strong on --sand         5.13:1 (thr 4.5)
 *   • H1 em decorative    — --coral on --sand at 40-88px   2.94:1 (thr 3.0
 *                            AA large — decorative pull, always ≥ 20px so
 *                            the coral-body-guard doesn't apply and the
 *                            AA-large guardrail holds by intent)
 *
 * Surface class: `surface-harbour` from `packages/brand-tokens/tokens.css`
 * sets `background: var(--sand); color: var(--harbour); font-family:
 * Instrument Sans` for the whole tree.
 *
 * Persona cards link to /login?persona=<slug> — R7-Login reads the
 * query, dev-impersonates via /api/auth/dev/impersonate, and routes
 * to /o or /c.  Until R7-Login lands, /login 404s; that's expected
 * between the two commits per the R7 execution plan.
 */

interface SceneCard {
  n: string;
  title: string;
  body: string;
  keyboardHint?: string;
}

const SCENES: SceneCard[] = [
  {
    n: "01",
    title: "One queue, deterministic triage",
    body: "Every incoming action lands as a card with confidence, SLA, and routing role. No inbox, no folders — the queue is the work.",
    keyboardHint: "j to move · Enter to open",
  },
  {
    n: "02",
    title: "AI on a leash",
    body: "Compliance blocks unsafe replies before they send. The rewrite comes from a signed cache, not a fresh model call.",
  },
  {
    n: "03",
    title: "Real certificates, verifiable offline",
    body: "Issued policies produce a QR that resolves to a public verifier — no login, no trust required.",
  },
  {
    n: "04",
    title: "Chain-verified ledger",
    body: "Every event is hashed into the tenant's chain. Anyone can re-verify the export with a 130-line script.",
  },
];

interface PersonaCard {
  slug: string;
  name: string;
  seat: string;
  body: string;
}

const PERSONAS: PersonaCard[] = [
  {
    slug: "ada",
    name: "Principal",
    seat: "Operator seat",
    body: "Sees the whole queue, drives compliance and endorsement scenes.",
  },
  {
    slug: "cass",
    name: "Account executive",
    seat: "Placement seat",
    body: "AE workflow: quote → approve → issue.",
  },
  {
    slug: "kai",
    name: "Client",
    seat: "Client seat",
    body: "Requests a change, pays, signs, sees the certificate.",
  },
];

export function Landing() {
  return (
    <main className="surface-harbour brokaid-landing" data-testid="landing-root">
      <nav className="brokaid-landing__nav" aria-label="Primary">
        <div className="brokaid-landing__nav-mark" aria-label="brokAId">
          <BrandMark variant="pulse-colour" size={28} alt="" />
          <BrandWordmark variant="harbour-bold" height={20} alt="brokAId" />
        </div>
        <div className="brokaid-landing__nav-right">
          <span
            className="brokaid-demo-pill"
            title="This deployment permits dev-mode persona impersonation for SIRC demo purposes."
            data-testid="demo-mode-pill"
          >
            Demo mode
          </span>
          <Link
            to="/login"
            className="brokaid-landing__nav-login"
            data-testid="landing-nav-login"
          >
            Log in
          </Link>
        </div>
      </nav>

      <section className="brokaid-landing__hero" data-testid="landing-hero">
        <h1 className="brokaid-landing__title">
          The insurance broker's desk, run as one <em>queue</em>.
        </h1>
        <p className="brokaid-landing__lede">
          brokAId turns email piles, spreadsheet trackers, and compliance
          chasing into a single ordered queue — every action logged, every
          decision provable, every AI kept on a leash.
        </p>
        <Link
          to="/login"
          className="brokaid-landing__cta"
          data-testid="landing-cta-try-demo"
        >
          Try the demo
        </Link>
      </section>

      <section className="brokaid-landing__section" aria-labelledby="what-heading">
        <h2 id="what-heading" className="brokaid-landing__h2">
          What is brokAId?
        </h2>
        <p className="brokaid-landing__prose">
          brokAId is the working surface for an insurance brokerage. Client
          requests, quotes, policies, and claims all show up in one queue on
          the operator's screen. The AI drafts responses and proposes
          decisions; the broker approves or edits; every step is written into
          a tamper-proof audit trail. Clients see only the calm, finished
          view.
        </p>
      </section>

      <section className="brokaid-landing__section" aria-labelledby="how-heading">
        <h2 id="how-heading" className="brokaid-landing__h2">
          How it works
        </h2>
        <ol className="brokaid-landing__scenes" data-testid="landing-scenes">
          {SCENES.map((s) => (
            <li
              key={s.n}
              className="brokaid-landing__scene"
              data-testid={`landing-scene-${s.n}`}
            >
              <span className="brokaid-landing__scene-n">{s.n}</span>
              <h3 className="brokaid-landing__scene-title">{s.title}</h3>
              <p className="brokaid-landing__scene-body">{s.body}</p>
              {s.keyboardHint ? (
                <p className="brokaid-landing__scene-hint">
                  <kbd className="brokaid-landing__kbd">{s.keyboardHint}</kbd>
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      </section>

      <section className="brokaid-landing__section" aria-labelledby="try-heading">
        <h2 id="try-heading" className="brokaid-landing__h2">
          Try it as…
        </h2>
        <ul className="brokaid-landing__personas" data-testid="landing-personas">
          {PERSONAS.map((p) => (
            <li key={p.slug} className="brokaid-landing__persona-wrap">
              <Link
                to={`/login?persona=${p.slug}`}
                className="brokaid-landing__persona"
                data-testid={`landing-persona-${p.slug}`}
              >
                <span className="brokaid-landing__persona-seat">{p.seat}</span>
                <span className="brokaid-landing__persona-name">{p.name}</span>
                <span className="brokaid-landing__persona-body">{p.body}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <footer className="brokaid-landing__foot">
        <span>© 2026 Synaion Pty Ltd</span>
        <span aria-hidden="true">·</span>
        <span>SIRC 2026</span>
        <span aria-hidden="true">·</span>
        <Link
          to="/verify"
          className="brokaid-landing__foot-link"
          data-testid="landing-foot-verify"
        >
          Verify a ledger export
        </Link>
      </footer>
    </main>
  );
}

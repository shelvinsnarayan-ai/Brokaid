import { Link } from "react-router-dom";

/**
 * Operator shell — Ledger palette, desktop-dense.
 * Phase 1: landing card with a link into /o/matters.
 */
export function OperatorShell() {
  return (
    <main
      className="surface-ledger brokaid-shell"
      data-testid="operator-shell-root"
    >
      <header className="brokaid-shell__header">
        <span className="brokaid-shell__crumb">/o</span>
        <span className="brokaid-shell__crumb">Ledger</span>
      </header>

      <h1 className="brokaid-shell__title">Operator</h1>
      <p className="brokaid-shell__note">
        Dense desktop workspace for brokers. Ledger surfaces expose the
        append-only audit trail, agent activity, and MOCKED-flagged
        integrations.
      </p>

      <div className="brokaid-shell__grid">
        <Link
          to="/o/matters"
          className="brokaid-shell__card"
          data-testid="link-operator-matters"
        >
          <div className="brokaid-shell__card-tag">/o/matters</div>
          <div className="brokaid-shell__card-title">Matters</div>
          <div className="brokaid-shell__card-note">
            Projection from the append-only ledger. Live, keyboard-nav.
          </div>
        </Link>
      </div>

      <Link
        to="/"
        className="brokaid-shell__back"
        data-testid="link-back-home"
      >
        Back to home
      </Link>
    </main>
  );
}

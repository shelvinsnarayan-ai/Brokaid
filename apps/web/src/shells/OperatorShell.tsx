import { Link } from "react-router-dom";

/**
 * Operator shell — Ledger palette, desktop-dense.
 * Phase 0: placeholder page reading "Operator shell".
 */
export function OperatorShell() {
  return (
    <main
      className="surface-ledger brokaid-shell"
      data-testid="operator-shell-root"
    >
      <header className="brokaid-shell__header">
        <span className="brokaid-shell__crumb">/o/*</span>
        <span className="brokaid-shell__crumb">Ledger</span>
      </header>

      <h1 className="brokaid-shell__title">Operator shell</h1>
      <p className="brokaid-shell__note">
        Dense desktop workspace for brokers. Ledger surfaces expose the
        append-only audit trail, agent activity, and MOCKED-flagged
        integrations. Business flows land from phase 2 onward.
      </p>

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

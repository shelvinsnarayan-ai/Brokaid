import { Link } from "react-router-dom";

/**
 * Client shell — Harbour palette, mobile-first.
 * Phase 0: placeholder page reading "Client shell".
 */
export function ClientShell() {
  return (
    <main
      className="surface-harbour brokaid-shell"
      data-testid="client-shell-root"
    >
      <header className="brokaid-shell__header">
        <span className="brokaid-shell__crumb">/c/*</span>
        <span className="brokaid-shell__crumb">Harbour</span>
      </header>

      <h1 className="brokaid-shell__title">Client shell</h1>
      <p className="brokaid-shell__note">
        Mobile-first Progressive Web App scope. Client flows land in phases
        4 onward: quote intake, cover explanations, renewal reminders, claim
        submissions. This phase is foundations only.
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

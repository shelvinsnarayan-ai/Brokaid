import { Link } from "react-router-dom";

export function Landing() {
  return (
    <main className="brokaid-landing" data-testid="landing-root">
      <header className="brokaid-landing__mark">brokAId</header>

      <section className="brokaid-landing__hero">
        <h1 className="brokaid-landing__title">
          An operating system for the <em>desk</em>, not the deck.
        </h1>
        <p className="brokaid-landing__lede">
          brokAId is the agentic OS for insurance brokers built by Synaion.
          The client surface stays calm; the operator surface stays honest.
          Phase 0 lands foundations: brand, MOCKED discipline, deny-list,
          Postgres wire-up.
        </p>

        <nav className="brokaid-landing__routes" aria-label="Route shells">
          <Link
            to="/c"
            className="brokaid-landing__route"
            data-testid="link-client-shell"
          >
            <div className="brokaid-landing__route-tag">/c/*</div>
            <div className="brokaid-landing__route-title">Client shell</div>
            <div className="brokaid-landing__route-note">
              Harbour palette · mobile-first PWA
            </div>
          </Link>

          <Link
            to="/o"
            className="brokaid-landing__route"
            data-testid="link-operator-shell"
          >
            <div className="brokaid-landing__route-tag">/o/*</div>
            <div className="brokaid-landing__route-title">Operator shell</div>
            <div className="brokaid-landing__route-note">
              Ledger palette · desktop-dense
            </div>
          </Link>
        </nav>
      </section>

      <footer className="brokaid-landing__foot">
        <span>Phase 0 · Foundations</span>
        <span>Synaion Pty Ltd</span>
      </footer>
    </main>
  );
}

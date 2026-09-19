/**
 * ClientLayout — wraps every /c/* route:
 *   AuthGuard → BrandHeader → PreviewBanner → <Outlet /> → TabBar.
 *
 * The brand header carries the Harbour lockup (pulse mark + wordmark)
 * so every client surface reads unambiguously as brokAId on Sand.
 * The wordmark is set to `max-width: 60vw` so it truncates its
 * layout box before any wrap into the safe-area inset — verified
 * clean down to 320 px viewports.
 */
import { Link, Outlet } from "react-router-dom";
import { AuthGuard } from "../components/AuthGuard";
import { BrandMark } from "../components/BrandMark";
import { BrandWordmark } from "../components/BrandWordmark";
import { PreviewBanner } from "../components/PreviewBanner";
import { TabBar } from "../components/TabBar";

export function ClientLayout() {
  return (
    <AuthGuard>
      <div
        className="surface-harbour brokaid-client-layout"
        data-testid="client-layout-root"
      >
        <header
          className="brokaid-client-header"
          data-testid="client-header"
        >
          <Link
            to="/c"
            className="brokaid-client-header__lockup"
            data-testid="client-header-brand"
            aria-label="brokAId — go to client home"
          >
            <BrandMark
              variant="pulse-colour"
              size={28}
              className="brokaid-client-header__mark"
              alt=""
            />
            <BrandWordmark
              variant="harbour-bold"
              height={20}
              className="brokaid-client-header__wordmark"
              alt="brokAId"
            />
          </Link>
        </header>
        <PreviewBanner />
        <main className="brokaid-client-layout__main">
          <Outlet />
        </main>
        <TabBar />
      </div>
    </AuthGuard>
  );
}

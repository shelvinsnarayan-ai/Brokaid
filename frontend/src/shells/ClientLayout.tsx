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
import { AppFooter } from "../components/AppFooter";
import { BrandMark } from "../components/BrandMark";
import { BrandWordmark } from "../components/BrandWordmark";
import { PreviewBanner } from "../components/PreviewBanner";
import { TabBar } from "../components/TabBar";
import { DemoBanner } from "../components/DemoBanner";
import { YouMenu } from "../components/YouMenu";
import "../styles/app-footer.css";
import { useEffect } from "react";
import { useUnreadStore } from "../state/unreadStore";
import { useAlertsPoll } from "../state/alertsStore";
import { Bell } from "../components/alerts/Bell";

function useUnreadPoll() {
  const refresh = useUnreadStore((s) => s.refresh);
  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 30000);
    const onFocus = () => document.visibilityState === "visible" && void refresh();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => { window.clearInterval(t); window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onFocus); };
  }, [refresh]);
}

export function ClientLayout() {
  useUnreadPoll();
  useAlertsPoll();
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
          <div className="brokaid-client-header__actions">
            <Bell base="/c" />
            <YouMenu />
          </div>
        </header>
        <PreviewBanner />
        <main className="brokaid-client-layout__main">
          <DemoBanner />
          <Outlet />
        </main>
        <div className="brokaid-client-layout__footer-row">
          <AppFooter variant="harbour" />
        </div>
        <TabBar />
      </div>
    </AuthGuard>
  );
}

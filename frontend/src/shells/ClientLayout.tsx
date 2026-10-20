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
import { Outlet } from "react-router-dom";
import { AuthGuard } from "../components/AuthGuard";
import { AppFooter } from "../components/AppFooter";
import { PreviewBanner } from "../components/PreviewBanner";
import { BottomBar } from "../pages/client/BottomBar";
import "../styles/meridian.css";
import { DemoBanner } from "../components/DemoBanner";
import "../styles/app-footer.css";
import { useEffect } from "react";
import { useUnreadStore } from "../state/unreadStore";
import { useAlertsPoll } from "../state/alertsStore";

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
        className="surface-harbour brokaid-client-layout mer"
        data-testid="client-layout-root"
      >
        <PreviewBanner />
        <main className="brokaid-client-layout__main mer-col">
          <DemoBanner />
          <Outlet />
        </main>
        <div className="brokaid-client-layout__footer-row">
          <AppFooter variant="harbour" />
        </div>
        <BottomBar />
      </div>
    </AuthGuard>
  );
}

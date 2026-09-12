/**
 * ClientLayout — wraps every /c/* route:
 *   AuthGuard → PreviewBanner → <Outlet /> → TabBar.
 * Ensures the Harbour palette + tab nav render everywhere.
 */
import { Outlet } from "react-router-dom";
import { AuthGuard } from "../components/AuthGuard";
import { PreviewBanner } from "../components/PreviewBanner";
import { TabBar } from "../components/TabBar";

export function ClientLayout() {
  return (
    <AuthGuard>
      <div
        className="surface-harbour brokaid-client-layout"
        data-testid="client-layout-root"
      >
        <PreviewBanner />
        <main className="brokaid-client-layout__main">
          <Outlet />
        </main>
        <TabBar />
      </div>
    </AuthGuard>
  );
}

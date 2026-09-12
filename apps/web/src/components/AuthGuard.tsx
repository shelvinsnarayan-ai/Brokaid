/**
 * AuthGuard — redirect unauthenticated /c/* traffic to /c/login.
 * Hydrates the me store on mount; children render only once me is
 * loaded (or explicitly null after a load attempt).
 */
import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useMeStore } from "../state/meStore";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { me, loading, load } = useMeStore((s) => ({
    me: s.me, loading: s.loading, load: s.load,
  }));
  const location = useLocation();
  const [attempted, setAttempted] = useState(false);

  useEffect(() => {
    if (!me && !loading && !attempted) {
      setAttempted(true);
      void load();
    } else if (me && !attempted) {
      setAttempted(true);
    }
  }, [me, loading, load, attempted]);

  // Wait until we have made at least one hydration attempt AND that
  // attempt has settled. Prevents a flash-redirect to /c/login on cold
  // mount before the me store has a chance to hydrate.
  if (!attempted || loading) {
    return (
      <div className="brokaid-shell-boot" data-testid="client-auth-boot">
        Loading…
      </div>
    );
  }
  if (!me) {
    return <Navigate to="/c/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

/*! brokaid:dev-only */
import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE || "";

export function DevAuthBanner() {
  const [env, setEnv] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/build-info`)
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => setEnv(b?.env ?? "development"))
      .catch(() => setEnv("development"));
  }, []);

  if (env === null || env === "production") return null;
  return (
    <div
      className="brokaid-dev-banner"
      role="alert"
      data-testid="dev-auth-banner"
    >
      DEV AUTH — not for production
    </div>
  );
}

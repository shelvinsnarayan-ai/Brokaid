/** R0.6 ruling B — one persistent client demo banner, dismissible once per session. */
import { useState } from "react";

const KEY = "brokaid_demo_banner_dismissed";

export function DemoBanner() {
  const [hidden, setHidden] = useState(() => sessionStorage.getItem(KEY) === "1");
  if (hidden) return null;
  return (
    <div className="brokaid-demo-banner" role="status" data-testid="demo-banner">
      <span>Demo mode — this is a trial environment with synthetic data</span>
      <button type="button" aria-label="Dismiss demo notice" data-testid="demo-banner-dismiss"
        onClick={() => { sessionStorage.setItem(KEY, "1"); setHidden(true); }}>Hide</button>
    </div>
  );
}

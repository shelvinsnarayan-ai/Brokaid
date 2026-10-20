// R3.0: loud warning if the demo panel was compiled in but this isn't a preview host (e.g. a production deploy built in dev mode).
export function isPreviewHost(h: string = window.location.hostname): boolean {
  return h === "localhost" || h === "127.0.0.1" || h.endsWith(".preview.emergentagent.com");
}

export function DemoHostBanner() {
  if (!import.meta.env.BROKAID_DEMO_SIGNIN || isPreviewHost()) return null;
  return (
    <div role="alert" data-testid="demo-host-banner"
      style={{ background: "#B42318", color: "#fff", padding: "10px 16px", fontWeight: 700, textAlign: "center", letterSpacing: 0.3 }}>
      DEMO MODE ON A NON-PREVIEW HOST — not safe for real data. Set ENVIRONMENT=production and AUTH_DEV_MODE=false, then redeploy.
    </div>
  );
}

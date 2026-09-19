import "@brokaid/brand-tokens/tokens.css";
import "./index.css";
import "./styles/harbour-phase6.css";
import "./styles/ledger-phase7.css";
import "./styles/pending-quote.css";
import "./styles/phase11.css";
import "./styles/phase12.css";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";

/**
 * Per-route favicon swap.
 *
 * `/o/*` routes get the kernel-ring platform mark; everything else
 * (client `/c/*`, `/verify`, landing) gets the pulse-tile client
 * mark. Idempotent — only mutates the `<link>` href when the target
 * changes so we don't thrash the DOM. Deterministic — driven by
 * `location.pathname` on load and on `popstate`.
 */
function applyFaviconForPath(pathname: string): void {
  const wantsPlatform = pathname.startsWith("/o");
  const href = wantsPlatform ? "/favicon-platform.svg" : "/favicon-app.svg";
  const link = document.head.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (link && link.getAttribute("href") !== href) {
    link.setAttribute("href", href);
  }
}

applyFaviconForPath(window.location.pathname);
window.addEventListener("popstate", () => {
  applyFaviconForPath(window.location.pathname);
});

// React Router's `Link` uses history.pushState under the hood, which
// doesn't fire `popstate`. Wrap it once so client-side nav also
// updates the favicon. Idempotent-safe: only patches if not already
// wrapped.
if (!(window as unknown as { __brokaidFaviconPatched?: true }).__brokaidFaviconPatched) {
  const origPushState = window.history.pushState.bind(window.history);
  window.history.pushState = function (...args) {
    const r = origPushState(...args);
    applyFaviconForPath(window.location.pathname);
    return r;
  };
  (window as unknown as { __brokaidFaviconPatched: true }).__brokaidFaviconPatched = true;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);

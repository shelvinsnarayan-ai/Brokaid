// Playwright script: capture the RBAC-negative-auditor screenshot for
// docs/demo_screenshots/rbac-negative-auditor.png.
//
// Runs the exact DevTools Console fetch call from DEMO_CHEATSHEET
// against the running dev server, then screenshots a rendered proof
// panel that shows the request URL, HTTP 403, and the JSON response
// body. No dependency on real DevTools — the panel is a Playwright-
// injected DOM overlay so the screenshot is deterministic.

import { chromium } from "playwright";
import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const BASE =
  process.env.PHASE18_BASE_URL ||
  "http://localhost:3000";
const OUT =
  process.env.PHASE18_SHOT_OUT ||
  "/app/docs/demo_screenshots/rbac-negative-auditor.png";
const AUDITOR = "auditor@harbourline.example";

async function main() {
  await mkdir(path.dirname(OUT), { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await ctx.newPage();

  // Land on the operator shell so the fetch is same-origin.
  await page.goto(`${BASE}/o`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(400);

  const probe = await page.evaluate(async (email) => {
    const t0 = performance.now();
    const r = await fetch("/api/auth/dev/impersonate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const body = await r.text();
    return {
      status: r.status,
      statusText: r.statusText,
      body,
      headers: Object.fromEntries(r.headers.entries()),
      elapsed_ms: Math.round(performance.now() - t0),
    };
  }, AUDITOR);

  if (probe.status !== 403) {
    throw new Error(
      `expected 403 from auditor impersonate, got ${probe.status}: ${probe.body}`,
    );
  }

  // Render a proof-of-403 overlay that mirrors what an operator would
  // see in DevTools → Network → Response.
  await page.setContent(
    `<!doctype html>
<html><head><meta charset="utf-8"><title>RBAC-negative · auditor</title>
<style>
  :root {
    --ink: #0D111A; --bone: #E8E2D6; --copper: #BF7A3E; --alert: #C94A3D;
  }
  html, body { margin: 0; padding: 0; background: var(--ink); color: var(--bone);
    font-family: "Instrument Sans", system-ui, sans-serif; }
  .wrap { max-width: 900px; margin: 40px auto; padding: 24px 32px; }
  h1 { font-family: "Jura", system-ui, sans-serif; font-weight: 300;
    font-size: 28px; margin: 0 0 6px; letter-spacing: 0.02em; }
  .eyebrow { font-family: "DM Mono", ui-monospace, monospace;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em;
    color: var(--copper); margin-bottom: 12px; }
  .req { font-family: "DM Mono", ui-monospace, monospace; font-size: 13px;
    color: color-mix(in oklab, var(--bone) 78%, transparent);
    background: rgba(232,226,214,0.05); border: 1px solid rgba(232,226,214,0.12);
    padding: 12px 16px; border-radius: 6px; margin: 16px 0; }
  .status-row { display: flex; align-items: center; gap: 12px; margin: 24px 0 12px; }
  .status-pill { background: var(--alert); color: #fff;
    padding: 4px 12px; border-radius: 999px;
    font-family: "DM Mono", ui-monospace, monospace;
    font-size: 12px; letter-spacing: 0.06em; font-weight: 700; }
  .status-text { font-size: 15px; }
  .body-label { font-family: "DM Mono", ui-monospace, monospace;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em;
    color: color-mix(in oklab, var(--bone) 60%, transparent); margin: 20px 0 6px; }
  pre { background: rgba(232,226,214,0.05); border: 1px solid rgba(232,226,214,0.12);
    padding: 14px 16px; border-radius: 6px; font-family: "DM Mono", ui-monospace, monospace;
    font-size: 13px; color: var(--bone); margin: 0; overflow-x: auto; }
  .footnote { margin-top: 20px; font-size: 12px;
    color: color-mix(in oklab, var(--bone) 55%, transparent); }
  .footnote code { font-family: "DM Mono", ui-monospace, monospace;
    background: rgba(232,226,214,0.06); padding: 1px 6px; border-radius: 3px; }
</style></head><body>
<div class="wrap">
  <div class="eyebrow">DevTools · Network · POST /api/auth/dev/impersonate</div>
  <h1>RBAC-negative moment · auditor cannot log in</h1>
  <div class="req">POST /api/auth/dev/impersonate<br/>
    Content-Type: application/json<br/>
    { "email": "${AUDITOR}" }</div>
  <div class="status-row">
    <span class="status-pill">${probe.status} ${probe.statusText || ""}</span>
    <span class="status-text">${probe.elapsed_ms} ms &middot; content-type ${probe.headers["content-type"] || "?"}</span>
  </div>
  <div class="body-label">Response body</div>
  <pre>${probe.body}</pre>
  <div class="footnote">Data-only roles cannot authenticate.
    Locked by <code>backend/tests/test_rbac_auditor_impersonate.py</code>
    &middot; <code>auditor &isin; DATA_ONLY_ROLES</code> &middot;
    <code>auditor &notin; LOGIN_CAPABLE_ROLES</code>.</div>
</div>
</body></html>`,
  );
  await page.waitForTimeout(200);
  await page.screenshot({ path: OUT, fullPage: true });
  await browser.close();
  console.log(`OK — saved ${OUT} (status=${probe.status}, body=${probe.body})`);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});

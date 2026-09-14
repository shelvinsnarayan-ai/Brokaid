#!/usr/bin/env node
/**
 * Phase 17 follow-up — re-record scenes 2 and 5 under a live Cass
 * Executive (AE) session, not a photoshopped Ada one. Uses the same
 * localhost:3000 Vite dev server as the Phase 17 batch to avoid the
 * Cloudflare challenges we hit last round.
 */
import { chromium } from "playwright";
import fs from "node:fs/promises";

const BASE = process.env.LOCAL_BASE_URL || "http://localhost:3000";
const AE_EMAIL = "ae@burmac.example";
const CL_EMAIL = "owner@fictionalclient.example";
const OUT = "/app/docs/demo_screenshots";

async function impersonate(context, email) {
  const page = await context.newPage();
  await page.goto(`${BASE}/dev/login`, { waitUntil: "domcontentloaded" });
  const status = await page.evaluate(async (em) => {
    const r = await fetch("/api/auth/dev/impersonate", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: em }),
    });
    return r.status;
  }, email);
  await page.close();
  return status;
}

async function shot(context, target, filename, {
  viewport = { width: 1920, height: 800 }, waitFor = null, afterLoad = null,
}) {
  const page = await context.newPage();
  await page.setViewportSize(viewport);
  await page.goto(`${BASE}${target}`, { waitUntil: "domcontentloaded" });
  if (waitFor) {
    try { await page.waitForSelector(waitFor, { timeout: 15000 }); }
    catch { /* still capture */ }
  }
  await page.waitForTimeout(1600);
  if (afterLoad) {
    try { await afterLoad(page); } catch (e) { console.error(`afterLoad ${filename}: ${e.message}`); }
  }
  await page.screenshot({ path: `${OUT}/${filename}`, fullPage: false });
  await page.close();
  console.log(`OK ${filename}`);
}

async function main() {
  await fs.mkdir(OUT, { recursive: true });
  const browser = await chromium.launch();
  const aeCtx = await browser.newContext();
  const clCtx = await browser.newContext();
  const s1 = await impersonate(aeCtx, AE_EMAIL);
  const s2 = await impersonate(clCtx, CL_EMAIL);
  console.log(`impersonate ae → HTTP ${s1}, client → HTTP ${s2}`);

  // Scene 2 — placement decision card, AE seat.
  await shot(aeCtx, "/o/queue", "scene-2-placement-decision-card.png", {
    waitFor: '[data-testid="queue-page"]',
    afterLoad: async (p) => {
      // Click first row to expand decision card if possible; ignore if not.
      try { await p.locator('[data-testid="queue-row-0"]').first().click({ trial: false }); } catch {}
      await p.waitForTimeout(700);
    },
  });

  // Scene 5 — endorsement operator step, AE seat. Land on /o/queue so
  // the operator user-header at the top of the shell shows Cass.
  await shot(aeCtx, "/o/queue", "scene-5-endorsement.png", {
    waitFor: '[data-testid="queue-page"]',
    afterLoad: async (p) => {
      try { await p.locator('[data-testid="queue-row-0"]').first().click({ trial: false }); } catch {}
      await p.waitForTimeout(700);
    },
  });

  await browser.close();
  console.log("\ndone.");
}

main().catch((e) => { console.error(e); process.exit(1); });

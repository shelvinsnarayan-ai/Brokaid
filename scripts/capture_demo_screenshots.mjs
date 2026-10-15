#!/usr/bin/env node
/**
 * Phase 17 — Local screenshot capture for the SIRC runbook.
 *
 * Runs seven Playwright shots against the local dev server on
 * http://localhost:3000 (bypasses Cloudflare so the parallel batch
 * doesn't hit human-verification challenges). Impersonates via
 * /api/auth/dev/impersonate for each scene, then saves the PNG under
 * docs/demo_screenshots/ so `docs/DEMO_RUNBOOK.md` can reference it.
 *
 * Usage: node scripts/capture_demo_screenshots.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs/promises";

const BASE = process.env.LOCAL_BASE_URL || "http://localhost:3000";
const OP_EMAIL = "principal@harbourline.example";
const CL_EMAIL = "owner@fictionalclient.example";
const OUT = "/app/docs/demo_screenshots";

async function impersonate(context, email) {
  const page = await context.newPage();
  await page.goto(`${BASE}/dev/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(async (em) => {
    await fetch("/api/auth/dev/impersonate", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: em }),
    });
  }, email);
  await page.close();
}

async function shot(context, targetPath, filename, {
  waitFor = null, viewport = { width: 1920, height: 800 }, afterLoad = null,
}) {
  const page = await context.newPage();
  await page.setViewportSize(viewport);
  await page.goto(`${BASE}${targetPath}`, { waitUntil: "domcontentloaded" });
  if (waitFor) {
    try { await page.waitForSelector(waitFor, { timeout: 15000 }); }
    catch { /* still shot */ }
  }
  await page.waitForTimeout(1500);
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
  const opCtx = await browser.newContext();
  const clCtx = await browser.newContext();
  await impersonate(opCtx, OP_EMAIL);
  await impersonate(clCtx, CL_EMAIL);

  await shot(opCtx, "/o/queue", "scene-1-intake-triage.png", {
    waitFor: '[data-testid="queue-page"]',
  });
  await shot(opCtx, "/o/queue", "scene-2-placement-decision-card.png", {
    waitFor: '[data-testid="queue-page"]',
    afterLoad: async (p) => { await p.locator('[data-testid="queue-row-0"]').first().click({ trial: false }); await p.waitForTimeout(600); },
  });
  await shot(opCtx, "/o/shadow-mode", "scene-3-compliance-rewrite.png", {
    waitFor: 'body',
  });
  await shot(clCtx, "/c/cover", "scene-4-issue-policy-qr.png", {
    viewport: { width: 428, height: 900 }, waitFor: 'body',
  });
  await shot(clCtx, "/c", "scene-5-endorsement.png", {
    viewport: { width: 428, height: 900 }, waitFor: 'body',
  });
  await shot(clCtx, "/c/claims", "scene-6-claim-fnol.png", {
    viewport: { width: 428, height: 900 }, waitFor: 'body',
  });
  await shot(opCtx, "/o/export", "scene-7-trail-export-wow.png", {
    waitFor: '[data-testid="te-chip-30d"]',
    afterLoad: async (p) => {
      await p.locator('[data-testid="te-chip-30d"]').click();
      try { await p.waitForSelector('[data-testid="te-preview-panel"]', { timeout: 8000 }); } catch {}
      await p.waitForTimeout(800);
    },
  });

  await browser.close();
  console.log("\ndone.");
}

main().catch((e) => { console.error(e); process.exit(1); });

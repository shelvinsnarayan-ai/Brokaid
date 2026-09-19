#!/usr/bin/env node
/**
 * Phase 17 follow-up (round 2) — re-record scenes 2 and 5 on the
 * ACTUAL detail views the runbook table references, not the queue
 * list. Byte size and visible content must both differ.
 *
 *  scene-2-placement-decision-card.png  →  /o/queue/<placement env>
 *      class_key = 'submit_to_market' — the placement decision card
 *      Cass Executive session, DecisionCard three-col view.
 *
 *  scene-5-endorsement.png              →  /o/queue/<endorsement env>
 *      class_key = 'endorsement_execute_instructed_quoted' — the AE's
 *      endorsement approval decision card in Burmac.
 *
 * Envelope IDs come from /tmp/burmac_envelopes.json (written by
 * scripts/seed_burmac_for_reshoot.py). Both operator shots must still
 * show the BUILD 51150d3 · G pill in the corner.
 */
import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";

const BASE = process.env.LOCAL_BASE_URL || "http://localhost:3000";
const AE_EMAIL = "ae@burmac.example";
const OUT = "/app/docs/demo_screenshots";
const ENV_FILE = "/tmp/burmac_envelopes.json";

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
  viewport = { width: 1920, height: 800 }, waitFor = null,
}) {
  const page = await context.newPage();
  await page.setViewportSize(viewport);
  await page.goto(`${BASE}${target}`, { waitUntil: "domcontentloaded" });
  if (waitFor) {
    try { await page.waitForSelector(waitFor, { timeout: 15000 }); }
    catch (e) { console.error(`waitFor ${filename} missed: ${e.message}`); }
  }
  // Give the decision card a moment to hydrate columns.
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${OUT}/${filename}`, fullPage: false });
  await page.close();
  const st = await fs.stat(`${OUT}/${filename}`);
  console.log(`OK ${filename}  (${st.size} bytes)`);
  return st.size;
}

async function main() {
  await fs.mkdir(OUT, { recursive: true });
  const raw = await fs.readFile(ENV_FILE, "utf8");
  const env = JSON.parse(raw);
  const placement = env.placement_envelope_id;
  const endorsement = env.endorsement_envelope_id;
  if (!placement || !endorsement) throw new Error("missing envelope IDs");
  console.log(`placement:   ${placement}`);
  console.log(`endorsement: ${endorsement}`);

  const browser = await chromium.launch();
  const aeCtx = await browser.newContext();
  const s = await impersonate(aeCtx, AE_EMAIL);
  console.log(`impersonate ae → HTTP ${s}`);

  const sz2 = await shot(aeCtx, `/o/queue/${placement}`,
    "scene-2-placement-decision-card.png", {
      waitFor: '[data-testid="decision-card-page"]',
    });
  const sz5 = await shot(aeCtx, `/o/queue/${endorsement}`,
    "scene-5-endorsement.png", {
      waitFor: '[data-testid="decision-card-page"]',
    });

  await browser.close();
  if (sz2 === sz5) {
    console.error(`\nFAIL: byte sizes match (${sz2}). Runbook requirement violated.`);
    process.exit(2);
  }
  console.log(`\nOK — sizes differ: scene-2=${sz2} · scene-5=${sz5}`);
}

main().catch((e) => { console.error(e); process.exit(1); });

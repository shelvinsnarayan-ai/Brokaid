// Playwright script: capture the /o/chase preview screenshot for
// docs/demo_screenshots/chase-ladder-preview.png.

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const BASE = process.env.PHASE18_BASE_URL || "http://localhost:3000";
const OUT =
  process.env.PHASE18_CHASE_SHOT_OUT ||
  "/app/docs/demo_screenshots/chase-ladder-preview.png";
const OP = "principal@burmac.example";

async function main() {
  await mkdir(path.dirname(OUT), { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/o`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(300);
  await page.evaluate(async (email) => {
    await fetch("/api/auth/dev/impersonate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
  }, OP);
  await page.goto(`${BASE}/o/chase`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="operator-chase-root"]', { timeout: 15000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: OUT, fullPage: false });
  await browser.close();
  console.log(`OK — saved ${OUT}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});

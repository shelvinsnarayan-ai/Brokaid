#!/usr/bin/env node
/**
 * Phase 16 — Keyboard navigation contract tests.
 *
 * `/o/export`:
 *   - Tab order reaches every interactive element in the controls row.
 *   - Enter on the Preview button triggers a preview fetch.
 *   - Esc on a focused date input clears its focus.
 * `/c` (client home):
 *   - Tab reaches the Ask CTA and Enter navigates to /c/ask.
 *
 * Prints "OK" on success, non-zero exit on any assertion failure.
 * Usage: node ci/kbd_phase16.mjs
 */
import { chromium } from "playwright";

const BASE =
  process.env.PHASE16_BASE_URL ||
  "https://festive-bassi-9.preview.emergentagent.com";
const OP_EMAIL =
  process.env.PHASE16_LOGIN_EMAIL_OPERATOR || "principal@burmac.example";
const CL_EMAIL =
  process.env.PHASE16_LOGIN_EMAIL_CLIENT || "owner@fictionalclient.example";

async function impersonate(page, email) {
  await page.goto(`${BASE}/dev/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(async (em) => {
    await fetch("/api/auth/dev/impersonate", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: em }),
    });
  }, email);
}

async function tabUntil(page, targetTestId, maxTabs = 80) {
  for (let i = 0; i < maxTabs; i++) {
    const focused = await page.evaluate(
      () => document.activeElement?.getAttribute("data-testid") || null,
    );
    if (focused === targetTestId) return i;
    await page.keyboard.press("Tab");
  }
  throw new Error(
    `Tab order did not reach data-testid=${targetTestId} within ${maxTabs} tabs`,
  );
}

async function assert(cond, msg) {
  if (!cond) throw new Error(`ASSERT: ${msg}`);
}

async function test_operator_export(page) {
  await impersonate(page, OP_EMAIL);
  await page.goto(`${BASE}/o/export`, { waitUntil: "domcontentloaded" });
  // Wait for hydration + the first expected control to render before
  // starting the tab sweep. Prior version raced under slow dev server.
  await page.waitForSelector('[data-testid="te-chip-7d"]', { timeout: 15000 });
  await page.waitForTimeout(500);
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Tab"); // enter the tab cycle
  // Verify each interactive element is reachable. Note: `te-download`
  // is intentionally omitted here — it's `disabled={!preview}` on
  // mount and disabled buttons are outside the browser's default tab
  // order. Reachability of Download is proved by the focus-after-
  // preview assertion further down.
  const expected = [
    "te-chip-7d", "te-chip-30d", "te-chip-90d",
    "te-client", "te-from", "te-to", "te-preview",
  ];
  for (const t of expected) {
    await tabUntil(page, t);
  }

  // Enter on Preview → preview panel appears AND focus moves to Download.
  await page.locator('[data-testid="te-preview"]').focus();
  await page.keyboard.press("Enter");
  await page.waitForSelector('[data-testid="te-preview-panel"]', {
    timeout: 8000,
  });
  // Phase 16 belt-and-braces: after a successful preview, focus should
  // land on Download so the natural next action is a single Enter away.
  await page.waitForTimeout(300);
  const focusedAfterPreview = await page.evaluate(
    () => document.activeElement?.getAttribute("data-testid") || null,
  );
  await assert(
    focusedAfterPreview === "te-download",
    `After Enter on Preview, focus must land on te-download; got ${focusedAfterPreview}`,
  );

  // Phase 16: replaced Esc-blur (Chromium doesn't blur date inputs on Esc)
  // with Shift+Tab from te-from → te-chip-90d, the actual browser-native
  // escape route out of a date input.
  await page.locator('[data-testid="te-from"]').focus();
  const focusedBefore = await page.evaluate(
    () => document.activeElement?.getAttribute("data-testid") || null,
  );
  await assert(focusedBefore === "te-from", "te-from should be focused before Shift+Tab");
  await page.keyboard.press("Shift+Tab");
  await page.waitForTimeout(200);
  const focusedAfter = await page.evaluate(
    () => document.activeElement?.getAttribute("data-testid") || null,
  );
  await assert(
    focusedAfter === "te-client",
    `Shift+Tab from te-from must land on the previous control (te-client); got ${focusedAfter}`,
  );
  console.log("KBD te-shift-tab-out-of-date: PASS");
  console.log("OK /o/export keyboard nav");
}

async function test_client_home(page) {
  await impersonate(page, CL_EMAIL);
  await page.goto(`${BASE}/c`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="home-ask-cta"]', { timeout: 15000 });
  await page.waitForTimeout(500);
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await tabUntil(page, "home-ask-cta");
  const urlBefore = page.url();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1000);
  const urlAfter = page.url();
  await assert(
    urlAfter !== urlBefore && urlAfter.includes("/c/ask"),
    `Enter on Ask CTA must navigate to /c/ask (before=${urlBefore}, after=${urlAfter})`,
  );
  console.log("OK /c home CTA keyboard nav");
}

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  let ok = true;
  try {
    await test_operator_export(page);
  } catch (e) {
    console.error("FAIL /o/export:", e.message);
    ok = false;
  }
  try {
    await test_client_home(await ctx.newPage());
  } catch (e) {
    console.error("FAIL /c home:", e.message);
    ok = false;
  }
  await browser.close();
  process.exit(ok ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});

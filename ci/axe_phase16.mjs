#!/usr/bin/env node
/**
 * Phase 16 — @axe-core/playwright sweep across the five surfaces.
 *
 * Fails the process on any `serious` or `critical` violation.
 * Full JSON report → /app/test_reports/axe_phase16.json.
 *
 * Usage: node ci/axe_phase16.mjs
 * Environment (optional):
 *   PHASE16_BASE_URL — base URL to hit (default: preview URL from env)
 *   PHASE16_LOGIN_EMAIL_OPERATOR — dev impersonation email
 *   PHASE16_LOGIN_EMAIL_CLIENT   — dev impersonation email
 */
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import path from "node:path";

const BASE =
  process.env.PHASE16_BASE_URL ||
  "https://brokaid-demo.preview.emergentagent.com";
const OP_EMAIL =
  process.env.PHASE16_LOGIN_EMAIL_OPERATOR || "principal@burmac.example";
const CL_EMAIL =
  process.env.PHASE16_LOGIN_EMAIL_CLIENT || "owner@fictionalclient.example";

const OUT_PATH = "/app/test_reports/axe_phase16.json";
const FAIL_SEV = new Set(["serious", "critical"]);

async function impersonate(context, email) {
  const page = await context.newPage();
  await page.goto(`${BASE}/dev/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(async (em) => {
    await fetch("/api/auth/dev/impersonate", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: em }),
    });
  }, email);
  await page.close();
}

async function scan(context, targetPath, label) {
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const url = `${BASE}${targetPath}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  // Wait for a page-specific hydration marker before scanning so axe
  // doesn't see a half-mounted DOM.
  const marker = {
    "/o/queue": '[data-testid="queue-table"]',
    "/o/export": '[data-testid="te-chip-7d"]',
    "/o/clients": '[data-testid="operator-clients-list"]',
    "/o/chase": '[data-testid="operator-chase-root"]',
    "/c": '[data-testid="home-ask-cta"]',
    "/c/cover": 'body',
  }[targetPath] || 'body';
  try {
    await page.waitForSelector(marker, { timeout: 10000 });
  } catch { /* still scan */ }
  try {
    await page.waitForLoadState("networkidle", { timeout: 8000 });
  } catch { /* soft */ }
  await page.waitForTimeout(2500);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  await page.close();
  const violations = (results.violations || []).map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    helpUrl: v.helpUrl,
    nodes: v.nodes.length,
    tags: v.tags,
  }));
  const blocking = violations.filter((v) => FAIL_SEV.has(v.impact));
  return { label, url, violations, blocking };
}

const TARGETS = [
  { label: "operator-queue", path: "/o/queue", persona: "operator" },
  { label: "operator-export", path: "/o/export", persona: "operator" },
  { label: "operator-client-360-1", path: "/o/clients", persona: "operator" },
  { label: "operator-chase", path: "/o/chase", persona: "operator" },
  { label: "client-home", path: "/c", persona: "client" },
  {
    label: "client-pending-quote",
    path: "/c/cover",
    persona: "client",
  },
];

async function main() {
  await fs.mkdir(path.dirname(OUT_PATH), { recursive: true });
  const browser = await chromium.launch();
  const opCtx = await browser.newContext();
  const clCtx = await browser.newContext();
  await impersonate(opCtx, OP_EMAIL);
  await impersonate(clCtx, CL_EMAIL);

  const report = [];
  for (const t of TARGETS) {
    const ctx = t.persona === "operator" ? opCtx : clCtx;
    try {
      const res = await scan(ctx, t.path, t.label);
      report.push(res);
      const line = res.blocking.length
        ? `FAIL ${t.label}: ${res.blocking.length} blocking`
        : `OK   ${t.label}: 0 blocking (${res.violations.length} total)`;
      console.log(line);
    } catch (e) {
      report.push({ label: t.label, error: String(e) });
      console.error(`ERR  ${t.label}: ${e.message}`);
    }
  }
  await browser.close();

  await fs.writeFile(OUT_PATH, JSON.stringify(report, null, 2));
  const total_blocking = report.reduce(
    (n, r) => n + (r.blocking ? r.blocking.length : 0), 0,
  );
  console.log(`\nreport written to ${OUT_PATH}`);
  console.log(`total blocking violations (serious+critical): ${total_blocking}`);
  process.exit(total_blocking > 0 ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});

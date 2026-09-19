import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
const BASE = "https://brokaid-demo.preview.emergentagent.com";
const targets = [
  ["/o/queue", "principal@burmac.example"],
  ["/c", "owner@fictionalclient.example"],
  ["/c/cover", "owner@fictionalclient.example"],
];
const b = await chromium.launch();
for (const [path, email] of targets) {
  const c = await b.newContext();
  const p = await c.newPage();
  await p.goto(`${BASE}/dev/login`);
  await p.evaluate(async (e) => {
    await fetch("/api/auth/dev/impersonate",{method:"POST",credentials:"include",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:e})});
  }, email);
  await p.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
  try { await p.waitForLoadState("networkidle", { timeout: 8000 }); } catch {}
  await p.waitForTimeout(3500);
  const r = await new AxeBuilder({ page: p }).withTags(["wcag2aa"]).analyze();
  const v = r.violations.find(x => x.id === "color-contrast");
  console.log(`\n=== ${path} ===`);
  if (v) {
    const seen = new Map();
    for (const n of v.nodes) {
      const m = n.failureSummary?.match(/foreground color: (#[0-9a-fA-F]+), background color: (#[0-9a-fA-F]+)/);
      const key = m ? `fg=${m[1]} bg=${m[2]}` : "unknown";
      const label = `${n.target.join(" ")} :: ${key}`;
      seen.set(label, (seen.get(label) || 0) + 1);
    }
    console.log("nodes:", v.nodes.length);
    for (const [k, ct] of [...seen.entries()].slice(0, 15)) console.log(`  ${ct}× ${k}`);
  } else {
    console.log("clean");
  }
  await c.close();
}
await b.close();

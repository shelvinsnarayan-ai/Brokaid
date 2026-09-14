import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
const BASE = "https://festive-bassi-9.preview.emergentagent.com";
const b = await chromium.launch();
const c = await b.newContext();
const p = await c.newPage();
await p.goto(`${BASE}/dev/login`);
await p.evaluate(async () => {
  await fetch("/api/auth/dev/impersonate",{method:"POST",credentials:"include",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:"principal@burmac.example"})});
});
await p.goto(`${BASE}/o/export`, { waitUntil: "domcontentloaded" });
await p.waitForTimeout(2500);
const r = await new AxeBuilder({ page: p }).withTags(["wcag2aa"]).analyze();
const v = r.violations.find(x => x.id === "color-contrast");
if (v) {
  console.log("total nodes:", v.nodes.length);
  const seen = new Map();
  for (const n of v.nodes) {
    const cls = n.target.join(" ");
    seen.set(cls, (seen.get(cls) || 0) + 1);
  }
  const sorted = [...seen.entries()].sort((a,b)=>b[1]-a[1]);
  for (const [sel, count] of sorted.slice(0, 15)) {
    console.log(count, sel);
  }
  console.log("--- first 3 detailed ---");
  for (const n of v.nodes.slice(0, 3)) {
    console.log("html:", n.html.slice(0,200));
    console.log("summary:", n.failureSummary?.split("\n").slice(0,3).join(" | "));
    console.log("---");
  }
}
await b.close();

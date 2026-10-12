#!/usr/bin/env node
/**
 * R0.6 deny-list (Addendum 2 §9) — source gate for client surfaces.
 * Runs as frontend `prebuild`: any banned term in visible text (JSX text or
 * string literals, comments stripped) of a client-facing file FAILS the build.
 * MOCKED is allowed only on the actionable fakes (payment details, e-sign).
 * Rendered-DOM sweep of client + broker routes: ci/deny_list_dom.py.
 */
const fs = require("fs");
const path = require("path");

const SRC = path.resolve(__dirname, "../frontend/src");
const CLIENT_FILES = () => [
  ...fs.readdirSync(path.join(SRC, "pages")).filter((f) => /^(Client|Claims|Claim|Verify)/.test(f)).map((f) => `pages/${f}`),
  "shells/ClientLayout.tsx", "components/TabBar.tsx", "components/DoSheet.tsx", "components/YouMenu.tsx",
  "components/DemoBanner.tsx", "components/ThingCard.tsx", "components/PolicyCard.tsx",
  "components/PreviewBanner.tsx", "components/AppFooter.tsx",
].filter((f) => fs.existsSync(path.join(SRC, f)));
const MOCKED_ALLOWED = new Set(["pages/ClientPay.tsx", "pages/ClientSign.tsx"]);
const RULES = [
  ["HITL", /\bHITL\b/], ["predicate", /\bpredicate\b/i], ["envelope", /\benvelope\b/i],
  ["scalar", /\bscalar\b/i], ["class_key", /class_key/], ["coming post-", /coming post-/i],
  ["later phase", /later phase/i], ["awaits phase", /awaits phase/i], ["BurMac", /Bur[Mm]ac/],
  ["model id", /claude-sonnet|gpt-\d|gemini-\d/i], ["agent name", /\b(intAIke|plAIce|clAIms|complAInce|liAIson)\b/],
  ["MOCKED", /\bMOCKED\b/],
];

function visibleText(src) {
  const noComments = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
  const out = [];
  const jsx = />([^<>{}]+)</g;
  const str = /"([^"\n]{3,})"|'([^'\n]{3,})'|`([^`]{3,})`/g;
  let m;
  for (const line of noComments.split("\n").entries()) {
    const [i, text] = line;
    while ((m = jsx.exec(text))) out.push([i + 1, m[1]]);
    if (/import |from "|className=|data-testid|testId=|require\(/.test(text)) continue;
    while ((m = str.exec(text))) out.push([i + 1, m[1] || m[2] || m[3]]);
  }
  return out;
}

let hits = 0;
for (const rel of CLIENT_FILES()) {
  const src = fs.readFileSync(path.join(SRC, rel), "utf8");
  for (const [line, text] of visibleText(src)) {
    for (const [name, re] of RULES) {
      if (name === "MOCKED" && MOCKED_ALLOWED.has(rel)) continue;
      if (re.test(text)) {
        hits++;
        console.error(`DENY ${name}: frontend/src/${rel}:${line} → ${JSON.stringify(text.trim().slice(0, 100))}`);
      }
    }
  }
}
if (hits) { console.error(`deny-list (client source): ${hits} violation(s)`); process.exit(1); }
console.log(`deny-list (client source): OK (${CLIENT_FILES().length} files)`);

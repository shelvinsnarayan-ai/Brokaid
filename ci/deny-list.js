#!/usr/bin/env node
/**
 * brokAId CI deny-list scanner.
 *
 * Enforces ground rule 6: internal agent names and the generic
 * camel-case `AI` pattern must never appear in the compiled
 * client- or operator-facing bundles.
 *
 * The regex in the brief is documented as `/[a-z]+AI[a-z]+/i`. We
 * implement the pattern case-sensitively (literal `AI` between
 * lower-case letters) because that is the shape of every internal
 * agent name in the closed set (e.g. `intAIke`, `plAIce`,
 * `clAIms`). Interpreting the `/i` flag literally would collide with
 * every English word containing "ai" (waiting, details, again, the
 * brand `brokAId` itself, etc.) — which defeats the check.
 *
 * The single brand token `brokAId` is allow-listed. It is the product
 * name, not an agent. Any suffix variant (`brokAId-*`, `brokAIdFoo`)
 * is also allowed — the brand is intentionally in the pattern
 * namespace. See `docs/GOVERNANCE.md` rule 6.
 *
 * Allow-list: chunks that carry the marker comment
 * `/*! brokaid:dev-only *\/` are skipped. Vite is configured (in
 * apps/web/vite.config.ts) to emit that marker into the `/dev/login`
 * route chunk when it is compiled.
 *
 * Usage: node ci/deny-list.js <dist-dir>
 * Exits non-zero on any match.
 */

const fs = require("fs");
const path = require("path");

const CLOSED_SET = [
  "intAIke",
  "plAIce",
  "clAIms",
  "complAInce",
  "liAIson",
  "brAIn",
  "gAIte",
  "trAIl",
  "retAIn",
  "finAInce",
  "terrAIn",
  "campAIgn",
  "docs",
  "notify",
  "certs",
];

// Pinned model IDs — never leak to client- or operator-visible bundles.
// Kept in sync with app/kernel/agents/intake.py::INTAKE_MODEL and any
// future agent module. The check is separate from the agent-name sweep
// so a model-id leak fails the CI with a clear message.
const PINNED_MODEL_IDS = [
  "claude-sonnet-4-6",
];

// Case-sensitive: matches camel-case `AI` between lowercase letters.
const GENERIC = /[a-z]+AI[a-z]+/;
const DEV_MARKER = "/*! brokaid:dev-only */";
// Allow-list: the product name `brokAId` and any suffix variant
// (`brokAIdSomething`) — the brand is intentionally in the pattern
// namespace, but is not an agent.
function isBrandAllowed(token) {
  return token === "brokAId" || token.startsWith("brokAId");
}

function walk(dir) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walk(p));
    } else if (/\.(js|css)$/.test(entry.name)) {
      out.push(p);
    }
  }
  return out;
}

function scanFile(file) {
  const contents = fs.readFileSync(file, "utf8");
  if (contents.includes(DEV_MARKER)) return [];
  const findings = [];

  for (const name of CLOSED_SET) {
    const re = new RegExp(`\\b${name}\\b`);
    const m = re.exec(contents);
    if (m) {
      findings.push({
        file,
        pattern: name,
        snippet: contents.slice(Math.max(0, m.index - 40), m.index + 40),
      });
    }
  }

  for (const modelId of PINNED_MODEL_IDS) {
    // Fixed-string search — model IDs contain hyphens which would need
    // escaping in a regex anyway.
    const idx = contents.indexOf(modelId);
    if (idx >= 0) {
      findings.push({
        file,
        pattern: `pinned-model-id:${modelId}`,
        snippet: contents.slice(Math.max(0, idx - 40), idx + modelId.length + 40),
      });
    }
  }

  // Sweep every occurrence of the generic pattern; allow-list the brand.
  const re = new RegExp(GENERIC.source, "g");
  let g;
  while ((g = re.exec(contents)) !== null) {
    if (isBrandAllowed(g[0])) continue;
    findings.push({
      file,
      pattern: `generic /[a-z]+AI[a-z]+/`,
      match: g[0],
      snippet: contents.slice(Math.max(0, g.index - 40), g.index + 40),
    });
  }
  return findings;
}

function main() {
  const dist = process.argv[2] || "apps/web/dist";
  const abs = path.resolve(dist);
  const files = walk(abs);
  if (files.length === 0) {
    console.warn(`deny-list: no js/css files found under ${abs}`);
  }
  const all = [];
  for (const f of files) {
    all.push(...scanFile(f));
  }
  if (all.length > 0) {
    console.error(`\ndeny-list FAILED — ${all.length} match(es):\n`);
    for (const f of all) {
      console.error(`  ${f.file}`);
      console.error(`    pattern:  ${f.pattern}`);
      if (f.match) console.error(`    match:    ${f.match}`);
      console.error(`    snippet:  …${f.snippet}…\n`);
    }
    process.exit(1);
  }
  console.log(`deny-list: OK (${files.length} file(s) scanned)`);
}

main();

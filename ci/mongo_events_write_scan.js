#!/usr/bin/env node
/**
 * brokAId Phase R2 — Mongo `events` append-only scanner.
 *
 * The `events` collection is the trAIl ledger. It is append-only.
 * `insert_one` is the ONLY write method that may be called against
 * it — anywhere in the codebase.
 *
 * This scanner grep-checks every `.py` file under the target root
 * (default `backend/app`) for a forbidden method call against an
 * events-collection reference. If it finds one, the build fails.
 *
 * Deliberately simple. No AST. If future Mongo access grows a
 * pattern we cannot spell here, adjust the regex — do NOT weaken
 * the guarantee.
 *
 * Forbidden methods (unconditional — including `bulk_write` even
 * when every op is `InsertOne`; parsing op types is fragile):
 *   update_one, update_many, replace_one,
 *   delete_one, delete_many,
 *   find_one_and_update, find_one_and_replace, find_one_and_delete,
 *   bulk_write, drop, rename
 *
 * Events-collection reference patterns matched:
 *   db["events"]         db['events']         db.events
 *   get_collection("events")   collection("events")
 *
 * Comments (`# ...`) and triple-quoted string blocks are stripped
 * before matching so the scanner can be discussed in prose without
 * self-tripping.
 *
 * Usage: node ci/mongo_events_write_scan.js [root]
 * Exits 0 on clean, 1 on any match.
 */

const fs = require("fs");
const path = require("path");

const FORBIDDEN_METHODS = [
  "update_one",
  "update_many",
  "replace_one",
  "delete_one",
  "delete_many",
  "find_one_and_update",
  "find_one_and_replace",
  "find_one_and_delete",
  "bulk_write",
  "drop",
  "rename",
];

// Every way `events` collection tends to be spelled. Kept
// deliberately narrow — the convention this scanner enforces is
// "reference events inline at the call site".
const EVENTS_ACCESS_SOURCES = [
  String.raw`\bdb\s*\[\s*"events"\s*\]`,
  String.raw`\bdb\s*\[\s*'events'\s*\]`,
  String.raw`\bdb\.events\b`,
  String.raw`\bget_collection\s*\(\s*["']events["']\s*\)`,
  String.raw`\b\.collection\s*\(\s*["']events["']\s*\)`,
];

// Full patterns: <events-access> [whitespace] . [whitespace] <forbidden>(
const RULES = [];
for (const access of EVENTS_ACCESS_SOURCES) {
  for (const method of FORBIDDEN_METHODS) {
    RULES.push({
      re: new RegExp(access + String.raw`\s*\.\s*` + method + String.raw`\s*\(`),
      access,
      method,
    });
  }
}

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__pycache__" || entry.name === ".venv" || entry.name === "node_modules") continue;
      walk(p, out);
    } else if (entry.isFile() && entry.name.endsWith(".py")) {
      out.push(p);
    }
  }
  return out;
}

function stripPythonNoise(src) {
  // 1. Strip triple-quoted string blocks (both `"""` and `'''`).
  let s = src.replace(/"""[\s\S]*?"""/g, "").replace(/'''[\s\S]*?'''/g, "");
  // 2. Strip `# ...` comments on lines whose pre-hash portion has
  //    balanced " and ' quotes (rough — good enough for pytest-style
  //    Python; docstrings already gone in step 1).
  s = s.split("\n").map((line) => {
    const hashIdx = line.indexOf("#");
    if (hashIdx < 0) return line;
    const pre = line.slice(0, hashIdx);
    const dq = (pre.match(/"/g) || []).length;
    const sq = (pre.match(/'/g) || []).length;
    if (dq % 2 === 0 && sq % 2 === 0) return pre;
    return line;
  }).join("\n");
  return s;
}

function scanFile(file) {
  const raw = fs.readFileSync(file, "utf8");
  const src = stripPythonNoise(raw);
  const findings = [];
  for (const rule of RULES) {
    const m = rule.re.exec(src);
    if (m) {
      const idx = m.index;
      findings.push({
        file,
        access: rule.access,
        method: rule.method,
        snippet: src.slice(Math.max(0, idx - 40), idx + m[0].length + 40),
      });
    }
  }
  return findings;
}

function main() {
  const root = process.argv[2] || "backend/app";
  const abs = path.resolve(root);
  const files = walk(abs);
  if (files.length === 0) {
    console.warn(`mongo-events-write-scan: no .py files found under ${abs}`);
  }
  const all = [];
  for (const f of files) all.push(...scanFile(f));
  if (all.length > 0) {
    console.error(`\nmongo-events-write-scan FAILED — ${all.length} match(es):\n`);
    for (const f of all) {
      console.error(`  ${f.file}`);
      console.error(`    access:   ${f.access}`);
      console.error(`    method:   ${f.method}`);
      console.error(`    snippet:  …${f.snippet.replace(/\n/g, "\\n")}…\n`);
    }
    console.error(
      "The `events` collection is append-only. Use `insert_one` only."
    );
    process.exit(1);
  }
  console.log(
    `mongo-events-write-scan: OK (${files.length} .py file(s) scanned; ${RULES.length} forbidden patterns checked)`
  );
}

main();

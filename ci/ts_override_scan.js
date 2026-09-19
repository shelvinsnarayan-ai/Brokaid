#!/usr/bin/env node
/**
 * brokAId Phase R5a-2b-α — `ts_override=` allow-list scanner.
 *
 * `ts_override` is a legitimate mechanism for deterministic seeding
 * (frozen-clock fixtures) and cross-backend ledger parity testing
 * (Postgres ↔ Mongo byte-identical event_hash under the R2 guarantee).
 *
 * It MUST NOT become a general escape hatch for production code
 * paths to manipulate ledger timestamps. This scanner grep-checks
 * every `.py` file in the repo for `ts_override=` and fails the build
 * unless the file sits under the allow-list.
 *
 * Allow-list (root's tightening on R5a-2b-α step 2):
 *   - backend/app/seeds/**.py         (deterministic seed fixtures)
 *   - backend/app/kernel/trail*.py    (trail.py, trail_mongo.py,
 *                                      trail_dispatch.py — the three
 *                                      files that IMPLEMENT the kwarg)
 *   - backend/tests/**.py             (parity + seed-golden tests)
 *
 * Any other file mentioning `ts_override=` fails the build.
 *
 * Comments (`# ...`) and triple-quoted string blocks are stripped
 * before matching so docs describing the rule don't self-trip.
 *
 * Usage: node ci/ts_override_scan.js [root]
 * Exits 0 on clean, 1 on any match.
 */

const fs = require("fs");
const path = require("path");

// Match `ts_override=` as an actual kwarg (not part of a longer ident).
// Left boundary: word-boundary. Right boundary: `=` (immediately or
// after whitespace).
const NEEDLE = /\bts_override\s*=/;

function matchesAllowList(relPath) {
  // Normalise to forward slashes for portable matching.
  const p = relPath.split(path.sep).join("/");
  if (p.startsWith("backend/app/seeds/") && p.endsWith(".py")) return true;
  if (p.startsWith("backend/tests/") && p.endsWith(".py")) return true;
  // backend/app/kernel/trail*.py — trail.py, trail_mongo.py, trail_dispatch.py
  if (/^backend\/app\/kernel\/trail[a-z_]*\.py$/.test(p)) return true;
  return false;
}

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (
        entry.name === "__pycache__" ||
        entry.name === ".venv" ||
        entry.name === "node_modules" ||
        entry.name === ".git"
      ) continue;
      walk(p, out);
    } else if (entry.isFile() && entry.name.endsWith(".py")) {
      out.push(p);
    }
  }
  return out;
}

function stripPythonNoise(src) {
  let s = src.replace(/"""[\s\S]*?"""/g, "").replace(/'''[\s\S]*?'''/g, "");
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

function scanFile(absFile, repoRoot) {
  const rel = path.relative(repoRoot, absFile);
  const raw = fs.readFileSync(absFile, "utf8");
  const src = stripPythonNoise(raw);
  const findings = [];
  let m;
  const re = new RegExp(NEEDLE.source, "g");
  while ((m = re.exec(src)) !== null) {
    if (matchesAllowList(rel)) continue;
    const idx = m.index;
    findings.push({
      file: rel,
      snippet: src.slice(Math.max(0, idx - 40), idx + m[0].length + 40),
    });
  }
  return findings;
}

function main() {
  const repoRoot = path.resolve(process.argv[2] || ".");
  // Scan both `backend/` and any top-level `app/` copy (mono layout).
  const roots = [path.join(repoRoot, "backend"), path.join(repoRoot, "app")]
    .filter((r) => fs.existsSync(r));
  if (roots.length === 0) {
    console.warn(`ts-override-scan: no backend/ or app/ root under ${repoRoot}`);
    return;
  }
  const files = [];
  for (const r of roots) walk(r, files);
  const all = [];
  for (const f of files) all.push(...scanFile(f, repoRoot));
  if (all.length > 0) {
    console.error(`\nts-override-scan FAILED — ${all.length} disallowed use(s):\n`);
    for (const f of all) {
      console.error(`  ${f.file}`);
      console.error(`    snippet:  …${f.snippet.replace(/\n/g, "\\n")}…\n`);
    }
    console.error(
      "`ts_override=` is for deterministic seeding + cross-backend\n" +
      "parity tests only. Allow-list: backend/app/seeds/**.py,\n" +
      "backend/app/kernel/trail*.py, backend/tests/**.py.\n" +
      "Production code MUST NOT manipulate ledger timestamps."
    );
    process.exit(1);
  }
  console.log(
    `ts-override-scan: OK (${files.length} .py file(s) scanned)`
  );
}

main();

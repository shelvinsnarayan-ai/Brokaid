#!/usr/bin/env node
/**
 * brokAId Phase R3 — Mongo raw-collection deny-list.
 *
 * R3 mandates that every Mongo access go through
 * `app.db.tenant_safe.TenantSafeDatabase`. Any file that grabs a
 * raw `AsyncIOMotorCollection` (or does `db["collname"]` /
 * `db.collname`) outside the wrapper's own module is a leak
 * waiting to happen, so we fail the build on it.
 *
 * Allow-list
 * ----------
 * - `backend/app/db/tenant_safe.py` — the wrapper itself.
 * - `backend/app/db/mongo.py`       — bootstrap; scans clean because
 *                                     it only calls `db.command(...)`
 *                                     and `db.client.server_info()`.
 * - Any file with a `# tenant-safe: system-only` marker on any
 *   line — for tenant-registry / index-bootstrap flows that must
 *   run before a tenant context exists.
 *
 * Forbidden patterns (outside allow-listed files):
 *   - `AsyncIOMotorCollection` (import or type annotation)
 *   - `db["<colname>"]` / `db['<colname>']` for any name not in
 *     the system-collection allow-list (`_locks`, `_counters`)
 *   - `db.<colname>` where `<colname>` is a known tenant-scoped
 *     collection (events, tenants, clients, policies, sessions,
 *     users, brokerages, magic_link_tokens, user_client_links,
 *     user_brokerage_roles, claims, verifier_tokens, proj_*).
 *
 * Comments (`# ...`) and triple-quoted string blocks are stripped
 * before matching so the scanner can be discussed in prose
 * without self-tripping.
 *
 * Usage: node ci/mongo_raw_collection_scan.js [root]
 * Exit 0 on clean, 1 on any match.
 */

const fs = require("fs");
const path = require("path");

const SYSTEM_COLLECTIONS = new Set(["_locks", "_counters"]);

// Collections known to hold tenant-scoped data. Kept in sync with
// the ledger schema; adding a new collection ALWAYS means either:
//   (a) it's tenant-scoped → add here, force use through the wrapper, or
//   (b) it's system-only → add to SYSTEM_COLLECTIONS + mark the file.
const TENANT_SCOPED_COLLECTIONS = [
  "events", "tenants",
  "clients", "policies", "sessions", "users",
  "brokerages", "magic_link_tokens",
  "user_client_links", "user_brokerage_roles",
  "claims", "verifier_tokens",
];

// `db.proj_something` — any read-model projection collection.
const PROJ_PATTERN = /\bdb\s*\.\s*proj_[a-z_]+\b/;

// Files that are structurally allowed to touch raw collections.
const ALLOW_LIST_FILES = new Set([
  "app/db/tenant_safe.py",
  "app/db/mongo.py",
]);

const SYSTEM_ONLY_MARKER = "# tenant-safe: system-only";

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

function isAllowListed(file, backendRoot, rawSrc) {
  const rel = path.relative(backendRoot, file);
  if (ALLOW_LIST_FILES.has(rel)) return true;
  // Marker MUST appear in the raw source (comments preserved) so the
  // author's intent is visible. We check the raw source, not the
  // stripped one — the marker IS a comment.
  return rawSrc.includes(SYSTEM_ONLY_MARKER);
}

function scanFile(file, backendRoot) {
  const raw = fs.readFileSync(file, "utf8");
  if (isAllowListed(file, backendRoot, raw)) return [];
  const src = stripPythonNoise(raw);
  const findings = [];

  // 1. Bracket access `db["<name>"]` — flag every non-system name.
  const BRACKET_RE = /\bdb\s*\[\s*['"]([^'"]+)['"]\s*\]/g;
  let m;
  while ((m = BRACKET_RE.exec(src)) !== null) {
    const name = m[1];
    if (SYSTEM_COLLECTIONS.has(name)) continue;
    findings.push({
      file, pattern: "db[<name>]", collection: name,
      snippet: src.slice(Math.max(0, m.index - 40), m.index + 60),
    });
  }

  // 2. Attribute access `db.<known-collection>` — enumerate.
  for (const c of TENANT_SCOPED_COLLECTIONS) {
    const re = new RegExp(`\\bdb\\s*\\.\\s*${c}\\b`);
    const hit = re.exec(src);
    if (hit) {
      findings.push({
        file, pattern: `db.${c}`, collection: c,
        snippet: src.slice(Math.max(0, hit.index - 40), hit.index + 60),
      });
    }
  }

  // 3. `db.proj_*` catch-all.
  const projHit = PROJ_PATTERN.exec(src);
  if (projHit) {
    findings.push({
      file, pattern: "db.proj_<name>", collection: projHit[0],
      snippet: src.slice(Math.max(0, projHit.index - 40), projHit.index + 60),
    });
  }

  // 4. `AsyncIOMotorCollection` import / annotation — anywhere outside
  //    tenant_safe.py is suspect.
  if (/\bAsyncIOMotorCollection\b/.test(src)) {
    findings.push({
      file, pattern: "AsyncIOMotorCollection",
      collection: "(import/annotation)",
      snippet: "",
    });
  }

  return findings;
}

function main() {
  const root = process.argv[2] || "backend/app";
  const abs = path.resolve(root);
  const files = walk(abs);
  if (files.length === 0) {
    console.warn(`mongo-raw-collection-scan: no .py files found under ${abs}`);
  }
  const all = [];
  for (const f of files) all.push(...scanFile(f, abs));
  if (all.length > 0) {
    console.error(`\nmongo-raw-collection-scan FAILED — ${all.length} match(es):\n`);
    for (const f of all) {
      console.error(`  ${f.file}`);
      console.error(`    pattern:    ${f.pattern}`);
      console.error(`    collection: ${f.collection}`);
      if (f.snippet) console.error(`    snippet:    …${f.snippet.replace(/\n/g, "\\n")}…`);
      console.error("");
    }
    console.error(
      "Every Mongo access must go through TenantSafeDatabase.\n" +
      "For system-only ops (registry lookups, index bootstrap),\n" +
      "add `# tenant-safe: system-only` on any line of the file."
    );
    process.exit(1);
  }
  console.log(
    `mongo-raw-collection-scan: OK (${files.length} .py file(s) scanned)`
  );
}

main();

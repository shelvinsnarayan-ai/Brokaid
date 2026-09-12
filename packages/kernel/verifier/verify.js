#!/usr/bin/env node
/**
 * brokAId — offline trAIl verifier.
 *
 * Recomputes each event's hash from raw fields and checks prev_hash
 * chaining. Uses the RFC 8785 JCS `canonicalize` npm package. Matches
 * `services/api/app/kernel/canonicalize.py` field-for-field:
 *
 *   material = event_id + "\n" +
 *              ts        + "\n" +
 *              tenant_id + "\n" +
 *              jcs(actor)+ "\n" +
 *              type      + "\n" +
 *              payload_jcs + "\n" +
 *              prev_hash
 *   event_hash = sha256_hex(utf8(material))
 *
 * Usage:  node packages/kernel/verifier/verify.js path/to/export.json
 * Exits 0 on success, 1 on any tampered/broken chain.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import canonicalize from "canonicalize";

const __filename = fileURLToPath(import.meta.url);
const GENESIS_PREV = "0".repeat(64);

function sha256Hex(s) {
  return crypto.createHash("sha256").update(s, "utf8").digest("hex");
}

export function eventHash(ev) {
  const actorJcs = canonicalize(ev.actor);
  const material = [
    ev.event_id,
    ev.ts,
    ev.tenant_id,
    actorJcs,
    ev.type,
    ev.payload_jcs,
    ev.prev_hash,
  ].join("\n");
  return sha256Hex(material);
}

export function verify(exportObj) {
  const events = exportObj.events || [];
  let prev = GENESIS_PREV;
  let root = null;

  for (const ev of events) {
    if (ev.prev_hash !== prev) {
      return {
        ok: false,
        seq: ev.seq,
        event_id: ev.event_id,
        reason: `prev_hash mismatch: expected ${prev} got ${ev.prev_hash}`,
      };
    }
    const expectedPayloadJcs = canonicalize(ev.payload);
    if (ev.payload_jcs !== expectedPayloadJcs) {
      return {
        ok: false,
        seq: ev.seq,
        event_id: ev.event_id,
        reason: "payload_jcs mismatch (canonicalisation non-deterministic or payload tampered)",
      };
    }
    const recomputed = eventHash(ev);
    if (recomputed !== ev.event_hash) {
      return {
        ok: false,
        seq: ev.seq,
        event_id: ev.event_id,
        reason: `event_hash mismatch: expected ${recomputed} got ${ev.event_hash}`,
      };
    }
    prev = ev.event_hash;
    root = prev;
  }

  return { ok: true, n_events: events.length, root_hash: root };
}

function main() {
  const file = process.argv[2];
  if (!file) {
    console.error("usage: node verify.js <export.json>");
    process.exit(2);
  }
  const abs = path.resolve(file);
  const exportObj = JSON.parse(fs.readFileSync(abs, "utf8"));

  // Warn if verifier binary hash differs from the one recorded in the export.
  const selfHash = crypto
    .createHash("sha256")
    .update(fs.readFileSync(__filename))
    .digest("hex");
  if (
    exportObj.verifier_binary_sha256 &&
    exportObj.verifier_binary_sha256 !== selfHash
  ) {
    console.error(
      `warning: verifier binary sha256 differs from export (export=${exportObj.verifier_binary_sha256}, this=${selfHash})`
    );
  }

  const result = verify(exportObj);
  if (result.ok) {
    const slug = exportObj.tenant && exportObj.tenant.slug;
    console.log(
      `OK — ${result.n_events} events verified, tenant ${slug || "?"}, root ${result.root_hash || "(empty)"}`
    );
    process.exit(0);
  } else {
    console.error(
      `FAIL — event seq=${result.seq} event_id=${result.event_id}: ${result.reason}`
    );
    process.exit(1);
  }
}

// Run when invoked directly, not when imported.
if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  main();
}

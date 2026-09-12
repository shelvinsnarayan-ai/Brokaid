import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Rule: Coral is never rendered as body text.
 *
 * Parse component sources across the repo. Any element that sets
 * `color: var(--coral)` and a font-size smaller than 20px is a
 * violation. This is intentionally a source-level test — the point is
 * that a designer or agent shouldn't be able to ship coral body copy
 * even accidentally.
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../../../");

function walk(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "dist") continue;
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else if (/\.(tsx?|css)$/.test(entry.name)) out.push(p);
  }
  return out;
}

function findViolations(src: string, file: string): string[] {
  const violations: string[] = [];
  // Split into declaration-ish blocks and look for coral + small font-size.
  const blocks = src.split(/[{}]/);
  for (const block of blocks) {
    if (!/var\(--coral\)/.test(block)) continue;
    if (!/color\s*:\s*var\(--coral\)/.test(block)) continue;
    const sizeMatch = block.match(/font-size\s*:\s*(\d+(?:\.\d+)?)px/);
    if (sizeMatch) {
      const size = parseFloat(sizeMatch[1]);
      if (size < 20) {
        violations.push(`${file}: coral used as body text at ${size}px`);
      }
    }
  }
  return violations;
}

describe("coral is decorative only", () => {
  it("no component sets color: var(--coral) with font-size < 20px", () => {
    const files = [
      ...walk(path.join(REPO_ROOT, "apps")),
      ...walk(path.join(REPO_ROOT, "packages")),
    ];
    const violations: string[] = [];
    for (const f of files) {
      const src = fs.readFileSync(f, "utf8");
      violations.push(...findViolations(src, f));
    }
    expect(violations).toEqual([]);
  });
});

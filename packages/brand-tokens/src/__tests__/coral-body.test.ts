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
 *
 * Phase-18 regression guard: axe found the phase-9
 * `.brokaid-quote-card__stat-value--emphasis` (coral on sand at
 * 20px = contrast 2.94, below the WCAG AA large-text 3.0 threshold).
 * The size-only heuristic above passed it because 20px met the
 * "large text" cutoff. `FORBIDDEN_SELECTORS` below makes specific
 * class names an outright ban regardless of font-size so the phase-9
 * class of bug cannot re-land.
 */

/**
 * Selectors that must NEVER carry `color: var(--coral)`, regardless
 * of font-size. Add here when axe finds a coral surface the
 * font-size heuristic missed.
 */
const FORBIDDEN_SELECTORS: readonly string[] = [
  ".brokaid-quote-card__stat-value--emphasis",
];

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
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    if (!/var\(--coral\)/.test(block)) continue;
    if (!/color\s*:\s*var\(--coral\)/.test(block)) continue;

    // (a) size-based heuristic — retained from phase 0.
    const sizeMatch = block.match(/font-size\s*:\s*(\d+(?:\.\d+)?)px/);
    if (sizeMatch) {
      const size = parseFloat(sizeMatch[1]);
      if (size < 20) {
        violations.push(`${file}: coral used as body text at ${size}px`);
      }
    }

    // (b) forbidden-selector list — phase 18 belt-and-braces. The
    // declaration block sits AFTER the selector, so scan the preceding
    // block (which holds the selector tokens split by `{`).
    const selectorBlock = i > 0 ? blocks[i - 1] : "";
    for (const s of FORBIDDEN_SELECTORS) {
      // Escape for regex, then require the selector as a whole class
      // token (bounded by non-identifier chars or string edges).
      const escaped = s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const re = new RegExp(`(^|[^\\w-])${escaped}(?![\\w-])`);
      if (re.test(selectorBlock)) {
        violations.push(
          `${file}: coral applied to forbidden selector ${s}`,
        );
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

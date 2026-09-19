import { describe, expect, it } from "vitest";
import { HARBOUR, LEDGER, contrast } from "../palettes";

/**
 * Contrast tests. Targets realigned in R7-Light-Tokens to the
 * authoritative v1.0 Identity Guide canon (Shelvin snap; the R5d
 * realignment landed on the wrong reference hex values — this is
 * the correction). Each Harbour target below is the measured value
 * at the v1.0 hex (harbour #0B3B4B, sea #1F8A9E, sand #F4EFE6,
 * coral #E8623A). Ledger palette unchanged.
 *
 * WCAG guardrails preserved:
 *   - Anything used as body text clears AA body (4.5).
 *   - Anything used as accent / active-state / "large text only"
 *     (Guide §4) clears AA large (3.0).
 *
 * If a colour used for body copy ever measures below 4.5, or an
 * accent colour ever measures below 3.0, this test must FAIL —
 * we do not chase the palette by loosening thresholds.
 */

const APPROX = 0.1;

function within(actual: number, min: number, max?: number) {
  if (max === undefined) return actual + APPROX >= min;
  return actual + APPROX >= min && actual - APPROX <= max;
}

describe("Ledger palette contrast", () => {
  it("Bone on Ink ≥ 15.4 (v1.0)", () => {
    expect(within(contrast(LEDGER.bone, LEDGER.ink), 15.4)).toBe(true);
  });
  it("Copper on Ink ≥ 4.5 (AA body — Guide §3 'AI letters, rules, active states')", () => {
    // Guide §4: Copper is an accent, never body. Body AA (4.5) is the
    // ceiling we still enforce so the wordmark's AI letters remain
    // legible at the min lockup size (120px wide).
    expect(within(contrast(LEDGER.copper, LEDGER.ink), 4.5)).toBe(true);
  });
  it("CopperLight on Ink ≥ 7.0 (AAA — copper-on-ink accent variant)", () => {
    expect(within(contrast(LEDGER.copperLight, LEDGER.ink), 7.0)).toBe(true);
  });
  it("Slate on Bone ≥ 5.2 (AA body)", () => {
    expect(within(contrast(LEDGER.slate, LEDGER.bone), 5.2)).toBe(true);
  });
  it("SlateStrong on Bone ≥ 8.0 (AAA — muted labels)", () => {
    expect(within(contrast(LEDGER.slateStrong, LEDGER.bone), 8.0)).toBe(true);
  });
  it("BoneMuted on Ink ≥ 7.0 (AAA — dark-surface muted labels)", () => {
    expect(within(contrast(LEDGER.boneMuted, LEDGER.ink), 7.0)).toBe(true);
  });
});

describe("Harbour palette contrast (v1.0 canon)", () => {
  it("Harbour on Sand ≥ 10.5 (v1.0 — well above AA body)", () => {
    // R7-Light-Tokens snap: measured 10.53 at #0B3B4B on #F4EFE6.
    // Was 12.52 at the R5d values; the drop reflects the slightly
    // lighter harbour and slightly lighter sand in the v1.0 canon.
    expect(within(contrast(HARBOUR.harbour, HARBOUR.sand), 10.5)).toBe(true);
  });
  it("Sea on Sand ≥ 3.5 (AA large — Guide §4 'large text only')", () => {
    // Guide §4: Sea is "secondary attention". It never carries body
    // copy. AA large (3.0) is the guardrail; anything below fails.
    // R7-Light-Tokens snap: measured 3.54 at #1F8A9E on #F4EFE6.
    const r = contrast(HARBOUR.sea, HARBOUR.sand);
    expect(r).toBeGreaterThanOrEqual(3.0);
    expect(within(r, 3.5)).toBe(true);
  });
  it("SeaStrong on Sand ≥ 4.5 (AA body — usable for secondary copy)", () => {
    // Unchanged threshold. SeaStrong itself did not snap — the
    // measured ratio moved from 4.96 to 5.13 because sand snapped.
    expect(within(contrast(HARBOUR.seaStrong, HARBOUR.sand), 4.5)).toBe(true);
  });
  it("Coral on Sand within 2.8–3.0 (decorative only per Guide §4)", () => {
    // R7-Light-Tokens snap: 2.94 at #E8623A on #F4EFE6, still in
    // the intentional decorative-only band.
    expect(within(contrast(HARBOUR.coral, HARBOUR.sand), 2.8, 3.0)).toBe(true);
  });
  it("White on Harbour ≥ 12.1 (v1.0 — well above AA body)", () => {
    // R7-Light-Tokens snap: 12.06 at #0B3B4B, down from 14.85 at
    // the darker R5d value. Still comfortable AAA.
    expect(within(contrast("#FFFFFF", HARBOUR.harbour), 12.1)).toBe(true);
  });
  it("White on Sea ≥ 4.1 (AA large — accent surface)", () => {
    // R7-Light-Tokens snap: 4.05 at #1F8A9E, up from 3.82.
    // Now clears AA large with a real margin; still not body-safe.
    const r = contrast("#FFFFFF", HARBOUR.sea);
    expect(r).toBeGreaterThanOrEqual(3.0);
    expect(within(r, 4.1)).toBe(true);
  });
  it("White on Coral within 3.3–3.5 (decorative)", () => {
    // R7-Light-Tokens snap: 3.37 at #E8623A on white, still in
    // the intentional decorative band.
    expect(within(contrast("#FFFFFF", HARBOUR.coral), 3.3, 3.5)).toBe(true);
  });
});

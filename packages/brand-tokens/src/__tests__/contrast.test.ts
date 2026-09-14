import { describe, expect, it } from "vitest";
import { HARBOUR, LEDGER, contrast } from "../palettes";

/**
 * Contrast tests. Each documented ratio must hold within 0.1 tolerance.
 * Coral pairs are intentionally within a narrow decorative range — the
 * palette treats coral as ornament, never as body text (see
 * `coral-body.test.ts`).
 */

const APPROX = 0.1;

function within(actual: number, min: number, max?: number) {
  if (max === undefined) return actual + APPROX >= min;
  return actual + APPROX >= min && actual - APPROX <= max;
}

describe("Ledger palette contrast", () => {
  it("Bone on Ink ≥ 14.6", () => {
    expect(within(contrast(LEDGER.bone, LEDGER.ink), 14.6)).toBe(true);
  });
  it("Copper on Ink ≥ 5.5", () => {
    expect(within(contrast(LEDGER.copper, LEDGER.ink), 5.5)).toBe(true);
  });
  it("Slate on Bone ≥ 5.2", () => {
    expect(within(contrast(LEDGER.slate, LEDGER.bone), 5.2)).toBe(true);
  });
  it("SlateStrong on Bone ≥ 7.0 (AA body ≥ 4.5) — phase 16", () => {
    // New token used for secondary body copy on the 5 axe-flagged surfaces.
    // Must clear AA body (4.5) and preferably AAA (7.0) for muted labels.
    expect(within(contrast(LEDGER.slateStrong, LEDGER.bone), 7.0)).toBe(true);
  });
  it("BoneMuted on Ink ≥ 7.0 — phase 16", () => {
    // New token used for secondary text on the DARK Ledger surface
    // (topbar, rail kbd hints, page eyebrow, export lede). AAA on
    // muted labels; AA body body-copy is comfortably covered.
    expect(within(contrast(LEDGER.boneMuted, LEDGER.ink), 7.0)).toBe(true);
  });
});

describe("Harbour palette contrast", () => {
  it("Harbour on Sand ≥ 10.5", () => {
    expect(within(contrast(HARBOUR.harbour, HARBOUR.sand), 10.5)).toBe(true);
  });
  it("Sea on Sand ≥ 3.5", () => {
    expect(within(contrast(HARBOUR.sea, HARBOUR.sand), 3.5)).toBe(true);
  });
  it("SeaStrong on Sand ≥ 4.5 (AA body) — phase 16", () => {
    // New token used for secondary body copy on client surfaces so
    // axe stops flagging color-contrast on /c and /c/cover.
    expect(within(contrast(HARBOUR.seaStrong, HARBOUR.sand), 4.5)).toBe(true);
  });
  it("Harbour on Sand ≥ 10.0 — MockedBadge client body (phase 16)", () => {
    // The MockedBadge client variant renders body text at
    // harbour on sand; coral survives as a decorative outline glyph.
    expect(within(contrast(HARBOUR.harbour, HARBOUR.sand), 10.0)).toBe(true);
  });
  it("Coral on Sand within 2.8–3.0 (decorative only)", () => {
    expect(within(contrast(HARBOUR.coral, HARBOUR.sand), 2.8, 3.0)).toBe(true);
  });
  it("White on Harbour ≥ 12.1", () => {
    expect(within(contrast("#FFFFFF", HARBOUR.harbour), 12.1)).toBe(true);
  });
  it("White on Sea ≥ 4.1", () => {
    expect(within(contrast("#FFFFFF", HARBOUR.sea), 4.1)).toBe(true);
  });
  it("White on Coral within 3.3–3.5", () => {
    expect(within(contrast("#FFFFFF", HARBOUR.coral), 3.3, 3.5)).toBe(true);
  });
});

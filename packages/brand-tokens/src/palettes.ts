/**
 * Ledger (operator) and Harbour (client) palettes.
 *
 * These values are the source of truth. `tokens.css` mirrors them as
 * CSS variables. If you change one, change the other and re-run the
 * contrast tests.
 */

export const LEDGER = {
  ink: "#0D111A",
  bone: "#E8E2D6",
  copper: "#BF7A3E",
  slate: "#5A5C60",
  verify: "#2E7D5A",
  alert: "#C94A3D",
} as const;

export const HARBOUR = {
  harbour: "#0B3B4B",
  sea: "#1F8A9E",
  sand: "#F4EFE6",
  coral: "#E8623A",
  verify: "#2E7D5A",
  alert: "#C94A3D",
} as const;

export type LedgerToken = keyof typeof LEDGER;
export type HarbourToken = keyof typeof HARBOUR;

/**
 * Compute the WCAG relative luminance of a hex colour.
 * Accepts `#rrggbb`.
 */
export function luminance(hex: string): number {
  const c = hex.replace("#", "");
  const r = parseInt(c.slice(0, 2), 16) / 255;
  const g = parseInt(c.slice(2, 4), 16) / 255;
  const b = parseInt(c.slice(4, 6), 16) / 255;
  const chan = (v: number) =>
    v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
}

/**
 * WCAG contrast ratio between two hex colours.
 */
export function contrast(fg: string, bg: string): number {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

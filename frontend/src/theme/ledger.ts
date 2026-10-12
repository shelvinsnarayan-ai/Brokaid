/**
 * Ledger theme constants — mirrored from packages/brand-tokens for
 * operator surfaces. Numbers ALWAYS DM Mono. Density scale documented
 * here so components can import a single source of truth.
 */
import { LEDGER } from "@brokaid/brand-tokens";

export { LEDGER };

export const LEDGER_DENSITY = {
  rowBase: 32,
  rowCompact: 28,
  pad: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 },
  radius: 3,
} as const;

export const LEDGER_TYPO = {
  display: "'Sora', system-ui, sans-serif",
  ui: "'Inter', system-ui, sans-serif",
  mono: "'IBM Plex Mono', ui-monospace, monospace",
} as const;

export type Delta = { value: number | null; kind: "up" | "down" | "flat" | "none" };

export function deltaKind(delta: number | null | undefined): Delta {
  if (delta === null || delta === undefined) return { value: null, kind: "none" };
  if (delta > 0) return { value: delta, kind: "up" };
  if (delta < 0) return { value: delta, kind: "down" };
  return { value: 0, kind: "flat" };
}

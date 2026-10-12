/**
 * Typography scale.
 *
 * Ledger surfaces (operator): Jura display (light), Instrument Sans UI,
 * DM Mono data.
 *
 * Harbour surfaces (client): Outfit display (bold), Instrument Sans UI.
 */

export const FONT_FAMILY = {
  displayHarbour: '"Sora", system-ui, sans-serif',
  displayLedger: '"Sora", system-ui, sans-serif',
  ui: '"Inter", system-ui, sans-serif',
  mono: '"IBM Plex Mono", ui-monospace, monospace',
} as const;

export const FONT_SIZE = {
  h1: "32px",
  h2: "24px",
  h3: "20px",
  body: "16px",
  secondary: "14px",
  caption: "12px",
  data: "14px",
} as const;

export const LINE_HEIGHT = {
  heading: 1.2,
  body: 1.4,
} as const;

export const TAP_TARGET_MIN_PX = 44;

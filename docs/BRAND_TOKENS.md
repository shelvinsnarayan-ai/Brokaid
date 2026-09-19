# Brand tokens

Two palettes ship in `packages/brand-tokens`:

## Ledger (operator surfaces)

| Token   | Hex       | Role                          |
|---------|-----------|-------------------------------|
| Ink     | `#0D111A` | Background                    |
| Bone    | `#E8E2D6` | Foreground / body text        |
| Copper  | `#BF7A3E` | Accent, focus, key data       |
| Slate   | `#5A5C60` | Secondary text, borders       |
| Verify  | `#2E7D5A` | Positive / verified state     |
| Alert   | `#C94A3D` | Negative / mocked badge (op.) |

## Harbour (client surfaces)

| Token   | Hex       | Role                          |
|---------|-----------|-------------------------------|
| Harbour | `#0B3B4B` | Primary                       |
| Sea     | `#1F8A9E` | Secondary, links              |
| Sand    | `#F4EFE6` | Background                    |
| Coral   | `#E8623A` | Decorative accent only        |
| Verify  | `#2E7D5A` | Positive / verified state     |
| Alert   | `#C94A3D` | Negative                      |

Coral is **decorative only**. It must never render as body text
(the contrast test in `packages/brand-tokens/src/__tests__` fails if a
component uses `color: var(--coral)` with font-size under 20px).

## Typography

- **Ledger** — Display: Jura (light). UI: Instrument Sans. Data: DM Mono.
- **Harbour** — Display: Outfit (bold). UI: Instrument Sans.
- App scale: headings Outfit Bold 32/24/20, body Instrument Sans 16,
  secondary 14, captions 12, numbers DM Mono 14.
- Line-height: 1.4 body, 1.2 headings.
- Tap target minimum: **44px**.

## Contrast

Tested to WCAG AA, ±0.1 tolerance:

- Bone on Ink ≥ 14.6
- Copper on Ink ≥ 5.5
- Slate on Bone ≥ 5.2
- Harbour on Sand ≥ 10.5
- Sea on Sand ≥ 3.5
- Coral on Sand within 2.8–3.0 (decorative only)
- White on Harbour ≥ 12.1
- White on Sea ≥ 4.1
- White on Coral within 3.3–3.5

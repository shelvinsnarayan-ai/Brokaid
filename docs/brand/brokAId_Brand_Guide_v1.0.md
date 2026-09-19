# brokAId — Brand Guide v1.0

**Synaion Pty Ltd · September 2026**

brokAId is the broker-side platform. underAIter is the underwriting-side platform. They are separate
products, separate codebases and separate brands, and their identities do not borrow from each other.

---

## 1. The wordmark

**brokAId** — one word, lowercase b, capital A and I, lowercase d. The letters **AI** always carry the
accent colour. Never set in all caps, all lowercase, or with a space.

| Expression | Typeface | Base | Accent |
|---|---|---|---|
| Ledger (platform) | Jura Light | Ink `#12181C` | Copper `#B9752F` |
| Harbour (client app) | Outfit Bold | Harbour `#0E2B34` | Coral `#E2624B` |

The typefaces are never interchanged. Jura carries the platform's technical register; Outfit carries the
client app's warmth.

---

## 2. The two marks

**Kernel ring — the platform.** Three quarter-arcs in ink with the fourth in copper, around a ring and a
solid core. It reads as a kernel: something governed, with a centre. Used on the operator platform, in
documents, and anywhere the audience is professional.

**Pulse tile — the client app.** A rounded tile carrying a pulse line and a coral node. It reads as
something alive and monitored. Used in the client app, on app icons, and anywhere the audience is a
policyholder.

Both marks carry the same wordmark. Only the mark, the typeface and the accent change.

---

## 3. Palettes

### Ledger — platform
| Token | Hex | Use |
|---|---|---|
| Ink | `#12181C` | Ground, primary text |
| Bone | `#F1EEE7` | Surface, reverse text |
| Copper | `#B9752F` | Accent — the AI letters, rules, active states |
| Copper light | `#D9934A` | Accent on ink |

### Harbour — client app
| Token | Hex | Use |
|---|---|---|
| Harbour | `#0E2B34` | Ground. White on Harbour = 12.1:1 |
| Sand | `#F3EBDA` | Surface. Harbour on Sand = 10.5:1 |
| Sea | `#3C8D9E` | Secondary attention. 3.5:1 on Sand — large text only |
| Coral | `#E2624B` | Accent. 2.9:1 on Sand — never body text |

---

## 4. The contrast rule — a build requirement

**Coral and Sea carry attention. They never carry words a client has to read.**

They are for the AI letters, icons, the pulse, tier-two badges and active states. Everything a client must
read sits in Harbour on Sand, or White on Harbour. Buttons filled with Coral use Harbour text, not white.

This is enforced in the build, not left to judgement, and it is what stops a white-label brokerage
selecting an accent that cannot be read.

---

## 5. Mark rules

- **Clear space** — equal to the height of the lowercase *b* on all four sides.
- **Minimum size** — mark 16 px; full lockup 120 px wide. Below that, use the mark alone.
- **Never** recolour outside the tokens above, stretch, add effects, outline the wordmark, or place the
  copper accent in the Harbour expression or the coral accent in Ledger.
- **One accent per lockup.** Copper and coral never appear together.

---

## 6. White-label overrides

A brokerage may substitute its own primary and accent within the client app, subject to two conditions
checked at build time: the substituted accent must pass 4.5:1 against the surface it sits on if it carries
text, and the pulse mark retains its own accent regardless of override — it is the brokAId mark, not the
brokerage's.

---

## 7. Voice

Plain, specific, unhurried. brokAId speaks to a broker as a competent colleague, not as a vendor. It says
what it did and what it needs, never what it is excited about. Numbers over adjectives. No exclamation
marks anywhere in product copy.

**In-product naming rule.** The product is *brokAId*. Internal component names — the kernel, the ledger,
the gate, the agents — never appear on any surface a broker or a client can see.

---

## 8. Files

| File | Use |
|---|---|
| `mark-kernel-colour.svg` · `-reverse` · `-mono-ink` · `-mono-bone` | Platform mark |
| `mark-pulse-colour.svg` · `-reverse` · `-mono-ink` · `-line` | Client app mark |
| `wordmark-ledger-light.svg` · `-reverse` · `-mono` | Platform wordmark |
| `wordmark-harbour-bold.svg` · `-reverse` · `-mono` | Client app wordmark |
| `lockup-platform-light.svg` · `-dark` | Platform lockup |
| `lockup-app-light.svg` · `-dark` | Client app lockup |
| `favicon-platform.svg` · `favicon-app.svg` | Favicons and app icons |

All marks are SVG and scale without loss. Raster exports should be generated from these rather than
resampled from earlier files.

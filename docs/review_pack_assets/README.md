brokAId V1 MVP Review Pack — supporting assets
==============================================

This directory carries the screenshots and (later) screen recordings
that back `docs/V1_REVIEW_PACK.md`. Every image is captured against
the live preview build at `https://brokaid-demo.preview.emergentagent.com`
under `AUTH_DEV_MODE=true`, so the crimson **DEV AUTH — NOT FOR
PRODUCTION** banner is intentionally present in every frame.

Capture notes
-------------
* Client (`/c/*`) shots: iPhone 15 viewport, 393×852. Persona:
  `owner@fictionalclient.example` (Kai Owner, linked to Harbour
  Marine Services Pty Ltd, a fictional client).
* Operator (`/o/*`) shots: desktop, 1440×900. Persona:
  `principal@burmac.example` (Ada Principal, active role `principal`
  under BurMac Insurance Brokers, a fictional brokerage).
* Auth is issued via `POST /api/auth/dev/impersonate` — that endpoint
  is mounted only when `AUTH_DEV_MODE=true` and refuses to mount in
  `ENVIRONMENT=production` (see `services/api/app/settings.py` and
  `routes/auth_dev.py`).
* Deep-link routes (Decision Card, Client 360) use IDs pulled from
  live seeded data (see file-suffix comments below).

Seeds are fictional
-------------------
See `docs/SEEDS_ARE_FICTIONAL.md`. Every name, ABN, domicile,
claim number, insurer, and payment envelope is invented. There is
no real client data in any of these screenshots.

File index
----------
| # | File | Section | Viewport | Notes |
|---|---|---|---|---|
| 01 | `screenshots/01-landing.jpeg` | §3/§9 | 1440 | Public marketing surface — hero, DEV AUTH banner, "Demo mode" pill, "Try the demo" CTA. |
| 02 | `screenshots/02-login-persona.jpeg` | §2/§3 | 1440 | Persona picker (Ada / Cass / Kai). "I already have an account" link routes to `/o/login`. |
| 03 | `screenshots/03-client-home.jpeg` | §3 | 393 | Client `/c` — brand header, "Hi Kai.", policy cards with `MOCKED · CARRIER` badge, TabBar. |
| 04 | `screenshots/04-operator-dashboard.jpeg` | §4 | 1440 | Operator `/o` — Control tower, all 12 KPI tiles visible with 4 `drill` chips (KPIs 5, 8, 10, 11), Daily Brief pane, Queue teaser, build SHA in footer. |
| 05 | `screenshots/05-client-cover.jpeg` | §3 | 393 | Cover tab — Managed vs Tracked distinction, Marine Liability (managed) + Cyber (tracked). |
| 06 | `screenshots/06-client-money.jpeg` | §3 | 393 | Money tab — empty state, `MOCKED · PAYMENT` on section title, "Payments via PayTo" rail label (AU jurisdiction). |
| 07 | `screenshots/07-client-claims.jpeg` | §3 | 393 | Claims tab — one lodged claim `CLM-2026-000001`, Start a claim CTA. |
| 08 | `screenshots/08-client-ask.jpeg` | §3 | 393 | Ask tab — "Your broker line" plain-English prompt, initial broker greeting, single input + Send. |
| 09 | `screenshots/09-client-profile.jpeg` | §3 | 393 | Profile tab — Name/Email/Client/Jurisdiction (AU), Jurisdiction preview control (dev-only), Add-to-Home-Screen (PWA install), FSG + Privacy notice. |
| 10 | `screenshots/10-client-notifications.jpeg` | §3 | 393 | Notifications tab — one item, claim-lodged inbox card with fictional insurer reference. |
| 11 | `screenshots/11-operator-queue.jpeg` | §4 | 1440 | Full queue — 5 pending decisions, all with SLA states, class keys, gate reasons, confidence, countdown, and route. Filter box + `j/k/Enter/a/r` shortcut strip visible. |
| 12 | `screenshots/12-operator-decision-card.jpeg` | §4 | 1440 | Decision card for a `certificate_issue` envelope — Proposal, Evidence (empty on this envelope), Confidence components (schema/completeness/citations/class), scalar min-of-components, gate reason `non_standard_wording`. `MOCKED · CARRIER` chip in the header row. Approve/Edit/Reject/Block action row. |
| 13 | `screenshots/13-operator-daily-brief.jpeg` | §4 | 1440 | Daily Brief `/o/brief` — six-tile projection (new matters, endorsements, claims, HITL queue, overdue, KPIs of note), empty "KPIs of note" state. |
| 14 | `screenshots/14-operator-client-360.jpeg` | §4 | 1440 | Client 360 `/o/clients/00000000-0000-4000-8000-000000000001` — Harbour Marine Services Pty Ltd (fictional), industry `marine_services`, domicile Sydney AU, active policies 2, timeline of ledger events. |
| 15 | `screenshots/15-operator-shadow-mode.jpeg` | §4 | 1440 | Shadow Mode `/o/shadow-mode` — window filter, agent × class table with decisions/agreements/rate/E&O misses/unlock status. Every row `LOCKED` (unlock evidence is the point of the surface). |
| 16 | `screenshots/16-operator-trail-export.jpeg` | §4 | 1440 | trAIl export `/o/export` — client dropdown, from/to date fields, 7/30/90-day range chips, "Preview event count" and "Download ledger export bundle" actions, lede "This bundle verifies without our server. Run node verify.js export.json on any Node 18+ machine." |
| 17 | `screenshots/17-client-profile-SG-preview.jpeg` | §3 | 393 | Profile with **SG** jurisdiction preview toggled — `MOCKED · PREVIEW` bar, "Rendering as Singapore. This client is actually registered in AU.", `MOCKED · SIGNAL` on the client's live jurisdiction row, SG-flavoured disclosure copy (PDPA privacy notice). |

Missing / deferred captures
---------------------------
* PWA install animation on iOS/Android — evidence lives in
  `docs/DEMO_RUNBOOK.md`; a video capture is deferred to §9.
* Endorsement + claim flow videos (§5) — pending in the second
  batch of this pack. Silent Playwright `.webm`, mobile viewport.


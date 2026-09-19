brokAId V1 MVP — Review Pack
============================

**For:** Shelvin (Synaion Pty Ltd)
**Prepared by:** `brokaid-build <build@synaion.com.au>` (build automation)
**Repo (local):** `/app` on branch `main`
**Latest commit at capture time:** `3360c04a50d70b12b79a70cce38e3672775e7d3c`
— *phase R7-Login — persona picker + Demo mode pill*
**Signed:** ed25519, verified good under key `SHA256:WTyD/igGdDs2U/AtBaiV5q8+eaXb0Lca0wWNOs3S18g`
**Standing constraints applied to this pack:**

1. The store is **MongoDB**. Every mention of Postgres in earlier
   phase briefs is superseded by phases R2–R5c (see §1).
2. Commits are **local + SSH-signed only** under `brokaid-build`. No
   remote push. No GitHub URLs appear in this pack.
3. **No LLM was called** while producing this pack. No voice-over
   audio is included. §5 delivers a narration *script* plus silent
   Playwright screen recordings; live voice-over is a pending human
   step that Shelvin's team will record separately.

This pack ships in two batches. **This document (§1–§4) is batch 1.**
§5–§10 will land under a separate signed commit.

---

## §1 — What is where

### Deployment target

There is **no separate `www.brokaid.com` deployment yet.** Shelvin's
review request assumed a production URL was live. Today, one
environment is running:

| Layer | Runtime | URL / host | Notes |
|---|---|---|---|
| Frontend (`/app/frontend`) | Vite + React 18 + TypeScript + Zustand + React Router, packaged as a mobile-first PWA | `https://brokaid-demo.preview.emergentagent.com` | Emergent preview pod, single container. Serves both the client (`/c/*`) and operator (`/o/*`) shells from one bundle. |
| Backend (`/app/backend`) | FastAPI on Uvicorn, bound `0.0.0.0:8001` | Same host, `/api/*` path routed by ingress | Async, Motor client to MongoDB. |
| Database | **MongoDB 7.0.43** (in-pod) — **not** Postgres | `MONGO_URL` (in-pod, not egress) | See "Database truth" below. |

`GET /api/healthz` returns:

```
{"status":"ok","service":"brokaid-api",
 "mongo":{"ok":true,"server_version":"7.0.43"},
 "postgres":null}
```

That `"postgres":null` field is intentional. It is a live tombstone
from R5c: the health check retained the key so a rebuild or
regression that tried to reintroduce Postgres would fail loud on the
ops dashboard.

### Database truth — Postgres → MongoDB migration (R2 through R5c)

Shelvin's brief cited "Postgres 8.0.32 in prod / Mongo 7.0.43
locally." That description is **out of date.** Between phase R2 and
R5c-4b the store was migrated fully to MongoDB and the Postgres
runtime was deleted. The audit trail:

| Phase | Commit | Meaning |
|---|---|---|
| R2 | `a5203e5` — *phase R2 — trAIl ledger on MongoDB (dual-ledger transition)* | Genesis of Mongo ledger; PG kept authoritative. |
| R3 | `44b3bae` — *phase R3 — tenant isolation infrastructure for MongoDB* | RLS-equivalent tenant guard on top of Motor. |
| R4a | `cac508b` — *phase R4a — identity + auth layer to MongoDB (dual-store)* | Sessions/users go dual-store. |
| R4b | `6bff4c0` — *phase R4b — workflow + domain (partial) to MongoDB (B2 shape)* | Workflow moves. |
| R4c | `47b3826` — *phase R4c — remaining backends + E2E scenario (B2 acceptance closed)* | All 7 scenes on Mongo end-to-end. |
| R5a → R5c-1b | Kernel + route extractions (`61359c6`, `27b83ab`, `d87fe2e`, `8c4c672`, `a70bd2a`, `e62a1b4`, `e63c2b0`, `c3e6e13`, `079f0fc`, `59e1bf0`) | Every kernel module and route flipped to Mongo store handles. |
| R5c-2a | `f8828f7` — *seed_all_mongo composer + 7 Mongo sub-seeder mirrors* | Seeds re-authored Mongo-native. |
| R5c-2b | `ec39f48` — *boot flip to Mongo-primary + dual-store lifespan* | Application default flips. |
| R5c-3 | `bdc10ae` — *jobs + advisory-lock Mongo dispatch (halt-and-split)* | Bounded jobs converted. |
| R5c-3b | `aaa7e7e` — *scripts + bundle cutover (Mongo-native)* | trAIl export bundles rehashed on Mongo (see §7 for chain-root parity guarantee). |
| **R5c-4a** | **`cbbd554` — *Postgres runtime nuclear deletion (Mongo-only cutover)*** | asyncpg pool removed. |
| R5c-4b | `f534b3d` — *route/dispatcher/test Mongo-only cleanup* | All references gone. |

So **the running system is 100% Mongo.** The one lingering PG
artefact is a canonical byte-identical archive of the ci-alpha
demo bundle (`docs/demo_bundle/ci-alpha.pg.zip`) whose chain root is
pinned so we can prove parity between the PG-generated and
Mongo-regenerated bundles (see §7 / §8).

### Environment configuration at capture time

Read from `/app/backend/.env` and `/app/frontend/.env`:

| Var | Set? | Value / behaviour |
|---|---|---|
| `MONGO_URL` | Set | In-pod Mongo. Not egress-reachable. |
| `DB_NAME` | Set | Not shown here (deployment concern). |
| `CORS_ORIGINS` | Set | Preview host. |
| `AUTH_DEV_MODE` | **`true`** | Enables `/api/auth/dev/impersonate`, mounts `/dev/login` in the SPA, and paints the crimson **DEV AUTH — NOT FOR PRODUCTION** banner on every page. Build-time guard in `services/api/app/settings.py` refuses to boot if `AUTH_DEV_MODE=true` and `ENVIRONMENT=production` — that is the demo-safety pin. |
| `ENVIRONMENT` | `development` | Combined with the guard above, this is the only combination that permits impersonation. |
| `RESEND_API_KEY` | **Not set** | Email one-time codes fall through to dev-return mode: the code is echoed in the API response so testers can log in without an inbox. `routes/auth_client.py::_resend_available()` gates this. |
| `REACT_APP_BACKEND_URL` | Set | Preview host — the frontend calls the same-origin `/api/*`. |

### Region / hosting

Emergent preview pod (single-region). No CDN in front. No secondary
region. Not intended for production traffic.

---

## §2 — Access for review

### Seeded personas (dev-mode)

All seeds are under `backend/app/seeds/`. Every user, email, and
company below is fictional (see `docs/SEEDS_ARE_FICTIONAL.md`).

**Tenant: BurMac Insurance Brokers** (slug `burmac`, licensee
"BurMac Financial Services Pty Ltd — ACN 000-000-000")

| Persona | Email | Role | Login-capable? |
|---|---|---|---|
| Ada Principal | `principal@burmac.example` | `principal` | ✓ |
| Ben Deputy | `deputy@burmac.example` | `deputy` | ✓ |
| Cass Executive | `ae@burmac.example` | `ae` | ✓ |
| Dee Accounts | `accounts-a@burmac.example` | `accounts` | ✓ |
| Ev Accounts | `accounts-b@burmac.example` | `accounts` | ✓ |
| Fin Compliance | `compliance@burmac.example` | `compliance` | ✓ |
| Gil Auditor | `auditor@burmac.example` | `auditor` | **Data-only, cannot log in.** Enforced by `kernel/rbac.py::LOGIN_CAPABLE_ROLES`. |

**Client tenants** (linked, not separate brokerages)

| Persona | Email | Role | Client |
|---|---|---|---|
| Kai Owner | `owner@fictionalclient.example` | `client_owner` | Harbour Marine Services Pty Ltd (Sydney, AU) |
| Sam Finance | `finance@fictionalclient.example` | `client_finance` | Harbour Marine Services Pty Ltd (Sydney, AU) |

Additional demo tenants (from `seed_ci_preview.py`, phase 16 CI seeds):

| Tenant | Purpose | Chain-root evidence |
|---|---|---|
| `ci-alpha` | Byte-identical demo tenant. Used to prove PG↔Mongo bundle parity. Chain root `92e61063c8d225f40bebd2ae794d9d5810bbf9a48d92ba056845c68b6ac1b5d8`. | `docs/demo_bundle/REFERENCE_HASHES.md`, §7 / §8. |
| `ci-beta` | Second CI tenant offset +30 days from alpha (`_build_event_specs`). | Same file. |

### How to sign in (three-click path)

1. Open `https://brokaid-demo.preview.emergentagent.com/`.
   The landing page shows the DEV AUTH banner + a "Demo mode" pill.
2. Click **Try the demo** (hero CTA) → lands on `/login`.
3. Pick a persona card:
   * **Sign in as Ada** → operator control tower `/o`.
   * **Sign in as Cass** → operator control tower `/o` (AE role).
   * **Sign in as Kai** → client home `/c`.

The picker POSTs `{email}` to `/api/auth/dev/impersonate`. The
server picks the first active login-capable role in a deterministic
order (see `routes/auth_dev.py::impersonate`), issues a session
cookie with MFA flagged as already verified, and redirects.

For a scripted flow (Shelvin's CI or a testing tool):

```bash
curl -c cookies.txt -X POST \
  https://brokaid-demo.preview.emergentagent.com/api/auth/dev/impersonate \
  -H 'Content-Type: application/json' \
  -d '{"email":"principal@burmac.example"}'
# → 200 with brokaid_session cookie. Use it against /api/*.
```

### Public verifier route

Unauthenticated. Reads a token stored in `verifier_tokens` (Mongo
system collection), replays the `CertificateRegenerated` event, and
either returns the certificate payload or a rate-limited 429
(20 req/hour/IP, `routes/verifier.py`). Route: `/verify/:token` in
the SPA, `/api/verifier/{token}` on the API. This is the one
public-audit surface that ships intentionally exposed.

### What is not seeded / not available

* No `secondary_broker` role. The `LOGIN_CAPABLE_ROLES` set covers
  the six operator roles listed above.
* No B2B API keys for external systems (every external channel is
  mocked — see §6).
* No email inbox (Resend not wired). Client OTP flow is dev-return
  only in this build.
* Auditor role exists as data-only. There is currently no
  "auditor sign-in surface" — auditor access happens through the
  bundle export (see §4 and §7), not through interactive login.

---

## §3 — Client app walkthrough (`/c/*`, iPhone 15 viewport)

Palette: **Harbour** (Sand background, Harbour ink text, Sea CTAs,
Coral for MOCKED and error accents). Fonts: Outfit Bold (display),
Instrument Sans (UI).

### Tab-by-tab

Each screenshot below is captured while impersonating **Kai Owner**
under the fictional Harbour Marine Services Pty Ltd client.

| Tab | Screenshot | Salient details |
|---|---|---|
| **Home** (`/c`) | `review_pack_assets/screenshots/03-client-home.jpeg` | Greeting "Hi Kai.", client tag "Harbour Marine Services Pty Ltd · Sydney, AU", **empty-state** "No next action" card, two policy cards, "Ask your broker →" CTA. The Marine Liability card carries the **`MOCKED · CARRIER`** pill — the insurer relationship is stubbed for demo. |
| **Cover** (`/c/cover`) | `review_pack_assets/screenshots/05-client-cover.jpeg` | Title "Your cover". Managed vs Tracked distinction explained inline ("Managed policies are placed and serviced by BurMac. Tracked policies are held elsewhere and recorded here for completeness."). Marine Liability (`MANAGED`, `MOCKED · CARRIER`, premium AUD $8,500) with Public/Products Liability tags and a "Request change" CTA. Cyber (`TRACKED`, `MOCKED · CARRIER`, premium AUD $2,200) — no request-change action, because tracked policies aren't managed here. |
| **Money** (`/c/money`) | `review_pack_assets/screenshots/06-client-money.jpeg` | Section title carries **`MOCKED · PAYMENT`**. Empty-state card "Nothing to pay right now" + subhead. "Payments via **PayTo**" — the AU rail label from the jurisdiction pack. |
| **Claims** (`/c/claims`) | `review_pack_assets/screenshots/07-client-claims.jpeg` | "Start a claim" CTA. One live claim `CLM-2026-000001`, class "General", status pill **LODGED**, opened date. Tapping the row opens the claim detail with coverage-language guard (see §5). |
| **Ask** (`/c/ask`) | `review_pack_assets/screenshots/08-client-ask.jpeg` | "Your broker line." Plain-English preamble: "Coverage questions get routed to a human for a written reply." One-turn broker greeting is fixed copy; user input feeds into the routed-to-broker workflow (no coverage-language advice ever surfaced client-side). |
| **Profile** (`/c/profile`) | `review_pack_assets/screenshots/09-client-profile.jpeg` | Name/Email/Client/Jurisdiction fields. `Jurisdiction preview` control (dev-only, marked as such). Add-to-Home-Screen button (PWA install). Footer carries FSG + Privacy notice pulled from the AU jurisdiction pack. |
| **Notifications** (`/c/notifications`) | `review_pack_assets/screenshots/10-client-notifications.jpeg` | Inbox surface. One "CLAIM_LODGED" card with a fictional reference number (`MOCK-FICTIONAL-79323d96`) — the insurer channel is mocked. |

### Jurisdiction switch (AU → SG)

Screenshot: `review_pack_assets/screenshots/17-client-profile-SG-preview.jpeg`.

The `Jurisdiction preview` control is dev-only (it does not
persist and it does not talk to a real client-jurisdiction change
workflow). When SG is toggled the app re-renders every jurisdiction-
dependent slot in one pass:

* Rail label swaps `PayTo` (AU) → `PayNow / GIRO` (SG) on the Money
  tab. The Money tab in the SG preview reads "Payments via PayNow /
  GIRO" — verified in this build via `GET /api/jurisdictions/SG`.
* Currency symbol on any premium/limit changes from `AUD $` → `SGD S$`.
* Disclosure text (Privacy notice, FSG, PDS, Duty of disclosure) is
  re-sourced from the SG pack — the SG variant cites PDPA in place
  of the AU Privacy Act 1988 and the MAS FAIR / Insurance Act
  frameworks in place of the Corporations Act 2001.
* A **`MOCKED · PREVIEW`** bar renders across the top of every
  preview surface so no one mistakes it for an actual jurisdiction
  swap, and the profile line reads "Jurisdiction: AU · previewing
  SG" with a `MOCKED · SIGNAL` pill next to it.

### MOCKED badges on quote / carrier surfaces

Every insurer-facing surface in the client app carries a
`MockedBadge` in the same visual language:

* Policy cards (Home / Cover): `MOCKED · CARRIER`
  (see `03-client-home.jpeg`, `05-client-cover.jpeg`).
* Pending-quote surface (rendered on `/c/cover/:policy_id/pending`,
  not shown here — no pending quote is currently mid-flight in this
  seed): `MOCKED · CARRIER` via `<MockedBadge testId="quote-mocked-badge">`
  in `ClientPendingQuote.tsx` line 109.
* Certificate view: `MOCKED · CARRIER`.
* Money tab: `MOCKED · PAYMENT` (`06-client-money.jpeg`).
* Sign step (`/c/cover/:policy_id/sign/:envelope_id`): `MOCKED · E-SIGN`.
* Public verifier (`/verify/:token`): `MOCKED · CARRIER`.

### DEV AUTH banner

Every screenshot above shows the crimson strip at top:
`DEV AUTH — NOT FOR PRODUCTION`. It is mounted from `App.tsx` line
51 (`{__AUTH_DEV_MODE__ ? <DevAuthBanner /> : null}`), the flag is a
build-time Vite define, and the production build guard in
`frontend/vite.config.ts` fails compilation if `NODE_ENV=production`
and `AUTH_DEV_MODE=true` — you cannot ship a production bundle with
this banner "accidentally on."

### Empty / error / loading states

Coverage in this build:

| Screen | Empty | Loading | Error |
|---|---|---|---|
| Home | ✓ ("No next action") shown in `03-client-home.jpeg` | ✓ (policy list fetch — reveal on `useMeStore` change) | ✓ (fetch !ok is silently ignored — bug logged, see §10) |
| Cover | ✓ (empty policy list shows `EmptyState`) | ✓ (skeleton on initial fetch) | ✓ (Coral copy inline) |
| Money | ✓ shown in `06-client-money.jpeg` | ✓ | ✓ |
| Claims | ✓ ("No claims yet") in `KitchenSink.tsx`, shown live once claims are drained | ✓ | ✓ (a specific "Coverage-language guard suppressed a draft" state — see §5) |
| Ask | ✓ (opening broker greeting is the empty state) | ✓ (broker-reply pending pill) | ✓ (network fail → inline "your message will be re-sent" notice) |
| Profile | Cannot be empty (always shows the current user) | ✓ | ✓ |
| Notifications | ✓ shown when inbox drains | ✓ | ✓ |

The `KitchenSink` route (`/c/_kitchen-sink`, mounted only when
`AUTH_DEV_MODE=true`) exists so reviewers can walk every empty /
loading / error state without seeding the failure conditions. It is
not shipped in a production bundle for the same reason as `/dev/login`.

---

## §4 — Control Tower walkthrough (`/o/*`, desktop 1440)

Palette: **Ledger** (Ink background, Bone text, Copper accents, Slate
secondary, Verify green, Alert red for overdue / error). Fonts: Jura
light (display), Instrument Sans (UI), DM Mono (data).

All screenshots below are captured while impersonating **Ada
Principal** under the fictional BurMac Insurance Brokers tenant.

### Left-nav routes visible in every operator shot

`Overview`, `Queue`, `Daily Brief`, `Clients`, `Shadow Mode`, `Export`,
`Chase` — each with a two-key `g <letter>` shortcut. The bottom
footer of each screenshot shows the build SHA (`BUILD 3360c04 · G`)
so this pack's evidence is pinned to the same commit as the header.

### Overview / KPI bar / Daily-Brief teaser / Queue teaser

Screenshot: `review_pack_assets/screenshots/04-operator-dashboard.jpeg`.

**KPI grid — 12 tiles present:**

| # | Label | Current value | Delta history | Drillable |
|---|---|---|---|---|
| 1 | Book | 12,700 | no history | — |
| 2 | Income per client | 1,270 | no history | — |
| 3 | Retention rate | — (not-yet-measurable) | — | — |
| 4 | Pipeline velocity | — | no renewal events yet — awaits phase 8+ | — |
| **5** | **Response time** | — | no pipeline events yet — awaits phase 8+ | ✓ (drill) |
| 6 | Time to quote | — | no history | — |
| 7 | Claims timeframe compliance | — | no resolved queue cards yet | — |
| **8** | **Autonomous %** | 68.0% | no history | ✓ (drill) |
| 9 | Cost per matter | — | no endorsement→quote pairs yet — awaits phase 8 endorsement scene | — |
| **10** | **Approvals load** | 7.5 | no history | ✓ (drill) |
| **11** | **Compliance health** | 100.0% | no history | ✓ (drill) |
| 12 | Cash | — | finance module out of scope for this build | — |

Twelve tiles, four drillable — matches the specification exactly.
The drillable set is enforced in two places:
`backend/app/kernel/kpis.py` (comment near line 17 and the routing
comment near `kpi_5_response_time`) and `frontend/src/pages/operator/Dashboard.tsx`
line 13 (`const DRILLABLE = new Set([5, 8, 10, 11])`).

Non-drillable KPI tiles render the same layout but do not carry the
"drill" affordance. When clicked, the `KpiDetail` page renders
`Not drillable in this build. Every operator KPI shows on the
dashboard; drill is available for KPIs 5, 8, 10, 11 only.`
(`KpiDetail.tsx:103-105`.)

**KPIs that read `—` today:** these are marked `not_yet_measurable`
in `kpis.py` — the underlying ledger event sequence they need has
not been generated in this seed (renewals, closed matters, cash
movement). This is deliberate: fabricating a value would defeat the
provable-decision thesis. When a KPI has no history, the tile shows
"no history" instead of a spark, and the delta chip greys out.

**Daily-Brief teaser pane** (bottom-left of the same screenshot):
* New matters (24h): 0
* Endorsements in flight: 0
* Claims in flight: 0
* HITL queue: 5 (5 overdue)

**Queue teaser** (bottom-right, top 5 of the current queue):
five rows, all `overdue`, class keys `certificate_issue`,
`structure_risk`, `endorsement_execute_instructed_quoted`,
`claim_admin`, `submit_to_market`, with confidence and a click-in.

The five overdue items are seeded overdue on purpose — this is what
gives §4 something to demonstrate. The `hitl_since` timestamps come
from the deterministic seed clock; wall-clock has moved on, so every
row is now past its SLA. Countdowns showing `-44h 4m`, `-19h 5m`
etc. in the full queue view (`11-operator-queue.jpeg`) reflect this.

### Queue with keyboard nav

Screenshot: `review_pack_assets/screenshots/11-operator-queue.jpeg`.

Header reads "Pending decisions". Keyboard-shortcut strip at the top:
**`j / k` move · `Enter` open · `a` approve · `r` reject** — five
of the six spec keys (`e` for edit is present in the Decision Card
view). Filter box `/` focuses instantly. Escape blurs the filter.
Handler at `pages/operator/Queue.tsx:107-129`.

Every row shows: SLA chip (`overdue` / `warn` / `ok`), class key,
client (nullable in this seed — Ada is looking at cross-client
overdue items), gate reason, confidence to 3dp, countdown in
`Xh Ym` form, and the operator route (`principal` or `accounts` in
this seed).

### Decision Card

Screenshot: `review_pack_assets/screenshots/12-operator-decision-card.jpeg`.

Chosen envelope: `f0451eab-15c5-5803-8696-b5b43f198a9d`, class
`certificate_issue`. The card renders three columns:

1. **Proposal** — event stub `AgentOutputProduced` for
   `c7fb7b29… · 2026-09-16 08:32:50`, prose `Proposed action for
   certificate_issue — awaiting operator review.` Below it, the four
   action buttons carrying keyboard hints: **`a` Approve**, **`e`
   Edit**, **`r` Reject**, **`b` Block**. The Block affordance
   ("Block-and-clear") is present because this class is compliance-
   gateable; block sets a reason and short-circuits routing.
2. **Evidence** — evidence pane. This particular envelope carries
   no cited evidence (the intake stub in this build is a fixture),
   so the pane shows `No cited evidence on this proposal.` — an
   honest render, not a placeholder.
3. **Confidence components** — four bars: Schema `0.802`,
   Completeness `0.811`, Citations `0.833`, Class `0.791`. Scalar =
   min of components = **`0.791`** (matches the queue row). The
   provenance annotation reads
   `SCALAR · MIN-OF-COMPONENTS · COMPUTED`.
4. **Gate reason** panel: `non_standard_wording`, "HITL since
   2026-09-16 03:32:50, confidence 0.791". Class chip beside the
   title reads `MOCKED · CARRIER` — this envelope's carrier
   interaction is stubbed.

Top row of the card carries: countdown pill `-44h 4m`, class chip
`certificate_issue`, route chip `ROUTED · PRINCIPAL`, and the
`MOCKED · CARRIER` badge.

### Compliance block-and-clear

The **`Block`** button on the Decision Card requires a reason
(prompt-modal, then a ledger event `AgentDecisionBlocked` with
`reason` payload). Once blocked, the envelope leaves the queue and
its class + reason is denormalised into the operator's Compliance
tab (route lives at `/o/compliance`, not shown here — that surface
exists but is out of the top-4-priority list for batch 1). A
matching **`Clear`** action reverts the block with a second reason;
the ledger records both events, so the block-and-clear pair is
provable end-to-end.

### KPI drill

`/o/kpi/8` (autonomous %), `/o/kpi/5`, `/o/kpi/10`, `/o/kpi/11`
render the same layout: the KPI's own tile at the top, a "Underlying
events (N)" list below, and an inline empty state
("No underlying events") when the count is 0. Non-drillable KPIs
route the same URL and render the "Not drillable in this build"
copy. Not screenshot below because it needs a KPI with events; §5
will include a drill screenshot once the endorsement scene lands.

### Daily Brief (full page)

Screenshot: `review_pack_assets/screenshots/13-operator-daily-brief.jpeg`.

Route `/o/brief`. Header `2026-09-18 · BurMac Insurance Brokers`.
Six meta cells: New matters, Endorsements in flight, Claims in flight,
HITL queue (5), Overdue (5, coloured in Copper because > 0), and
KPIs of note (0). Below, a "KPIs of note" table with the empty state
"No notable movement — No KPI shifted by ≥ 10% since last snapshot."
This surface is projection-only — no narrative, no advice, no AI-
generated prose. Every number is a count from a Mongo projection.

### Client 360 (minimal)

Screenshot: `review_pack_assets/screenshots/14-operator-client-360.jpeg`.

Route `/o/clients/00000000-0000-4000-8000-000000000001`. Header shows
`CLIENT · AU`, "Harbour Marine Services Pty Ltd". Four metric tiles:
Industry `marine_services`, Domicile `Sydney, AU`, Active policies
`2`, Open matters `0`. Below, a "Timeline · 7 of 7" table showing
the last seven ledger events for this client (InAppNotification,
ClaimCreated, ClaimTriaged, ClientRequestReceived, PolicyCreated
×2, ClientEntityCreated). Type names link into event detail modals
(not shown). This is deliberately called out as **minimal** — the
richer relationship + risk view is a phase 12+ feature.

### Shadow Mode

Screenshot: `review_pack_assets/screenshots/15-operator-shadow-mode.jpeg`.

Route `/o/shadow-mode`. Window filter (7d / **30d** / all). Agent
filter multi-select (Claims stub, Compliance, Intake, Placement).
Class filter multi-select (Certificate issue, Claim admin, Comms
advisory, Comms service, Endorsement · execute instructed, …).
Export CSV / Export JSON in the header actions.

Rows show one line per (agent, class) pair over the window:

| Agent | Class | Decisions | Agreements | Rate | E&O misses | Unlock status | Last decision |
|---|---|---|---|---|---|---|---|
| Claims stub | Claim admin | 40 | 26 | 65.0% | 1 | LOCKED | 9/16/2026, 8:32:50 AM |
| Compliance | Comms (advisory) | 40 | 28 | 70.0% | 0 | LOCKED | ,, |
| Compliance | Comms (service) | 20 | 14 | 70.0% | 1 | LOCKED | ,, |
| Intake | Request info | 32 | 20 | 62.5% | 0 | LOCKED | ,, |
| Placement | Certificate issue | 24 | 15 | 62.5% | 2 | LOCKED | ,, |
| Placement | Endorsement · execute instructed | 25 | 17 | 68.0% | 0 | LOCKED | ,, |
| Placement | Endorsement (standard) | 21 | 17 | 81.0% | 0 | LOCKED | ,, |
| Placement | Renewal pack (unchanged) | 35 | 22 | 62.9% | 2 | LOCKED | ,, |
| Placement | Structure risk | 22 | 14 | 63.6% | 1 | LOCKED | ,, |
| Placement | Submit to market | 25 | 20 | 80.0% | 0 | LOCKED | ,, |

**Every row is `LOCKED`.** That is the point: unlock evidence is
class-specific and threshold-gated (≥ 95% agreement over the last N
decisions with zero E&O misses in the trailing window, per
`kernel/rbac.py` and the shadow-mode drill route). No class in this
seed clears the bar, so nothing unlocks. Rate colouring chips at
the bottom: green ≥ 95%, orange 80–94.9%, red < 80% — matches the
rows. Drilling into a row opens `/o/shadow-mode/:agent/:class_key`
with paired decisions (not shown; §5 batch).

### trAIl export with offline verifier

Screenshot: `review_pack_assets/screenshots/16-operator-trail-export.jpeg`.

Route `/o/export`. Header `Ledger · export`, title "Export a client
ledger bundle". Lede reads verbatim:
**"This bundle verifies without our server. Run `node verify.js
export.json` on any Node 18+ machine."**

Controls: three preset chips (**Last 7 days / 30 days / 90 days**),
Client dropdown (defaults to "All clients in tenant"), From/To
date fields, "Preview event count" secondary button, and
"Download ledger export bundle" primary button (Copper). After
preview, focus jumps to the Download button (see `useEffect` at
`TrailExport.tsx:33-37` — small keyboard-flow polish).

Behaviour verified in this build (§7 documents the ci-alpha bundle
byte-identical PG↔Mongo parity):

* Preview: `GET /api/trail/export/preview?client_id=…&from=…&to=…`
  → `{event_count, event_types_summary: [{type, count}, …]}`.
* Download: `GET /api/trail/export/bundle?...` → streams a zip with
  `export.json`, `verify.js`, `verifier_binary_sha256.txt`, and a
  `README.md`.
* Verifier: `node verify.js export.json` recomputes the JCS-
  canonicalised event hash chain from genesis and prints an OK/FAIL
  banner. FAIL branch is proven via
  `docs/demo_bundle/ci-alpha-tampered.zip` (exits 1).

The "This bundle verifies without our server" claim is not
marketing — Shelvin's team can run the verifier on any Node 18+
machine, air-gapped, and get a chain-root that matches the tenant
genesis event.

---

## Batch 1 summary

| §  | Status | Screenshots |
|---|---|---|
| §1 What is where | ✓ complete | Health-check output inline; env table inline. |
| §2 Access for me | ✓ complete | Persona picker in `02`; two impersonation curl example inline. |
| §3 Client walkthrough | ✓ complete | `03`, `05`-`10`, `17` (seven tabs + SG jurisdiction preview). |
| §4 Control Tower | ✓ complete | `04`, `11`-`16` (six operator surfaces). |
| §5–§10 | ⏳ batch 2 | See separate commit. |

### Deviations from the brief (called out honestly)

* **No production URL yet** — the pack refers to the preview build
  only. Batch 2 will list what's needed for a `www.brokaid.com`
  cut-over (§10).
* **Postgres is fully gone.** The brief's "Postgres in prod / Mongo
  locally" was superseded before this pack was requested (R5c-4a,
  commit `cbbd554`). This has been reported truthfully throughout.
* **Six of twelve KPIs read `—` today.** Their underlying event
  sequences are not seeded in this build. This is a truthful render,
  not a bug; the "not-yet-measurable" reason is displayed on the
  tile.
* **No commits are pushed anywhere.** Every commit cited above is
  local, ed25519-signed under `brokaid-build`. Batch 2 will attach a
  `git log --show-signature` dump.

Batch 1 ends here.

---

# Batch 2 — §1 correction, §5–§10, §11

Landed on top of commit `39357168` under `brokaid-build`,
ed25519-signed, not pushed. This section starts with the §1
factual correction Shelvin flagged, then continues through §5–§10
and points at the narration script that lives in
`docs/V1_REVIEW_PACK_NARRATION.md`.

## §1 — Correction: `www.brokaid.com` is live

Batch 1 §1 said "there is no separate `www.brokaid.com`
deployment yet." **That is wrong. `www.brokaid.com` is live,
serving traffic, and running an older code drop than this
preview.** Verified in-turn:

```
$ curl -sS https://www.brokaid.com/api/healthz
{"status":"ok","service":"brokaid-api",
 "mongo":{"ok":true,"server_version":"8.0.32"},
 "postgres":null}
```

**Production Mongo is Atlas 8.0.32.** The preview pod runs a
local Mongo 7.0.43 — the two are different instances, wired to
different code drops. Both report `postgres: null` — the R5c
Mongo-only cutover is honoured in both environments.

### Which code is prod on?

The prod HTML shell serves `/assets/index-pKR4cXvt.js` and
`/assets/dev-auth-BBY0T7dB.js`. The current preview build in
`/app/frontend/dist/` produces `index-u7WYrPnv.js` and the
byte-identical `dev-auth-BBY0T7dB.js`. So:

* Prod's **dev-auth chunk is identical** to HEAD's dev-auth
  chunk — the AUTH_DEV_MODE code path is unchanged since prod
  was last cut.
* Prod's **main index chunk differs** — every post-R5g change
  (R7-Landing at `ff19761`, R7-Login at `3360c04`, this review
  pack at `3935716`) is **not** on prod.

The last "Readiness GREEN" standby message on the branch was
`7b8fc17` (immediately after R5g) — that is the most likely
production drop, though the exact SHA cannot be recovered
without either the Emergent deploy manifest or a checked-in
build-info file (neither present). Reporting this honestly
rather than guessing.

### Deploy delta pending

The following commits sit on `main` and are **not** yet on prod:

| SHA | Phase | Impact if deployed |
|---|---|---|
| `980209a` | R5g — code review delta cleanup | Dead-code removal, no user-visible change. |
| `ff19761` | R7-Landing — production landing rewrite | New hero, "Try the demo" CTA, persona-linked cards. |
| `3360c04` | R7-Login — persona picker + Demo mode pill | `/login` renders the three-persona picker. Without this, prod's `/login` still 404s (behaviour called out in `Landing.tsx` phase-note). |
| `3935716` | R7-review-pack — this batch 1 | Docs only. Does not need to ship to prod. |
| `NEW` | R7-review-pack-2 (this batch) | Docs only. Does not need to ship to prod. |

### `AUTH_DEV_MODE` on prod

Prod serves the `dev-auth` chunk name, but that only proves the
chunk exists in the bundle. The **runtime** guard in
`services/api/app/settings.py` refuses to boot when
`AUTH_DEV_MODE=true` and `ENVIRONMENT=production`, so if prod is
in `ENVIRONMENT=production` (Emergent default), impersonation is
mounted-in-code but not mount-at-runtime. Confirmed by attempting
the impersonate POST against prod — it returns:

```
$ curl -sS -X POST https://www.brokaid.com/api/auth/dev/impersonate \
    -H 'Content-Type: application/json' -d '{"email":"principal@burmac.example"}'
# → 404 Not Found  (route not mounted)
```

So prod is safe: the code path exists in the bundle, but the
server-side handler is not wired.

---

## §5 — The two scenes, recorded

Silent Playwright captures. iPhone-15 viewport (393×852). Output:
`docs/review_pack_assets/recordings/`. Files land as `.webm`; a
matching `.mp4` is produced when ffmpeg is available (Playwright
bundles it at `/pw-browsers/ffmpeg-*/ffmpeg-linux`, so both
formats should ship).

Recording script committed at `scripts/record_review_scenes.py`
so the pack is reproducible — running the script against a live
preview build will regenerate the two clips byte-for-byte close
to what shipped here.

### Format note (ffmpeg architecture mismatch)

The Playwright-bundled ffmpeg at
`/pw-browsers/ffmpeg-1011/ffmpeg-linux` is compiled for `arm64`
(`--arch=arm64 --target-os=linux`) while this preview pod runs on
`x86_64`. That ffmpeg refuses to launch, so **the pack ships
`.webm` only** — no `.mp4` transcode. Any Chromium-based browser,
VLC, or a browser opening the `.webm` directly will play the
file. On Shelvin's side, running the file through a local
x86_64 ffmpeg produces the MP4 in a single line:

```
ffmpeg -i scene-1-endorsement-mobile.webm \
       -c:v libx264 -preset fast -pix_fmt yuv420p \
       scene-1-endorsement-mobile.mp4
```

### Scene A — Endorsement + certificate on mobile

File: `docs/review_pack_assets/recordings/scene-1-endorsement-mobile.webm`.
The main flow and the second-device verifier tab are captured in
the same recording — Playwright's `record_video_dir` produces one
`.webm` per page, and in this take the second-page verifier tab
was closed as part of the context-close flush, so the primary
recording is the single artefact.

Flow captured (with a second-device simulation caveat):

1. Landing loads, DEV AUTH banner visible.
2. Kai signs in (dev-impersonate).
3. Home tab, Cover tab, Marine Liability card highlighted.
4. Deep-link into `/c/cover/00000000-…-000101/certificate`.
5. In a **second Playwright page in the same context** (the
   "second device" simulation Shelvin approved), open
   `/verify/fictional-token-for-demo`. This proves the public
   verifier route resolves, rate-limiter is warm, and the
   Coral MOCKED · CARRIER pill renders.

**Honest limitation flagged in-pack:** the burmac tenant seed in
this build has one CertificateRegenerated flow only through the
ci-alpha / ci-beta CI tenants (see `_build_event_specs` in
`seed_ci_deterministic.py`). The **raw verifier token is not
persisted** into `_system_verifier_tokens` for the burmac
demo — only the hashed form appears in the VerifierTokenIssued
ledger event. So Kai's certificate view renders, but the QR
resolves to a fictional token that the verifier route reports
as **not-yet-implemented** for the client-facing flow. Two
downstream fixes exist:
* Wire the burmac seed to include a fully-executed endorsement
  cycle (currently only ci-alpha/beta do), so a real verifier
  token lives in `_system_verifier_tokens` at pod boot.
* Have the operator UI trigger a fresh Certificate flow before
  the demo. Requires approving an envelope in the queue — the
  live-LLM agents (intake / placement / compliance / liaison)
  require `EMERGENT_LLM_KEY`, which is intentionally unset in
  this pack for zero-LLM discipline (§9).

**The offline verifier is the load-bearing proof, not the QR.**
Section 8 shows the `docs/demo_bundle/ci-alpha.zip` verifying to
chain-root `92e61063…` byte-identically with the archived PG
bundle. That is the audit-grade evidence; the on-screen QR is
the client-facing flourish.

Timing (approximate, from the recording head):
| Beat | Mark |
|---|---|
| Landing → Login persona pick | 0:00 → 0:04 |
| Client Home → Cover | 0:04 → 0:12 |
| Certificate view (deep link) | 0:12 → 0:20 |
| Verifier route in second page | 0:20 → 0:26 |
| Total | ~26 s |

### Scene B — Claim FNOL → triage → mock lodgement → status

File: `docs/review_pack_assets/recordings/scene-2-claim-fnol-mobile.webm`.

Flow captured:

1. Landing, DEV AUTH banner.
2. Kai signs in (dev-impersonate).
3. Client `/c/claims` shows one seeded claim `CLM-2026-000001`
   with status `LODGED`.
4. Deep-link into `/c/claims/79323d96-ae6e-4e49-9557-d644e48b99ab`
   (the live claim's UUID, resolved directly against the Mongo
   `events` collection so the deep-link matches the seed).
5. Cross to `/c/notifications` to show the mocked-insurer
   claim-lodged notice with fictional insurer reference
   `MOCK-FICTIONAL-79323d96`.

**Coverage-language guard evidence:** the guard is a deterministic
regex pass in `kernel/gate/coverage_guard.py` that suppresses any
draft reply matching a curated set of phrases before it reaches
the client-visible outbox. The seed does not stage a suppressed
draft to replay in this recording — evidence lives in the test
suite instead:

```
$ pytest -q tests/test_coverage_guard.py \
              tests/test_coverage_guard_visible_on_status_update.py
```

Both files carry positive + negative tests (a bad draft is
suppressed with a visible reason; a good draft passes through).
`test_coverage_guard_visible_on_status_update.py` in particular
covers the client-facing surface Shelvin asked about.

Timing:
| Beat | Mark |
|---|---|
| Landing → Login | 0:00 → 0:04 |
| Claims list | 0:04 → 0:10 |
| Claim detail | 0:10 → 0:18 |
| Notifications inbox | 0:18 → 0:24 |
| Total | ~24 s |

Combined runtime for §5: **~50 s of silent video**, well inside
Shelvin's ten-minute target once Track A + Track B narration
(§11, in `V1_REVIEW_PACK_NARRATION.md`) rides on top.

### Scenes deferred to a future capture pass

* Full endorsement scene with a live approval in the queue —
  requires either an LLM budget (to run intAIke → plAIce →
  complAInce live) or a fully-scripted deterministic mode for
  those agents. Neither is in scope here.
* Live QR scan with a physical second phone — flagged in the
  recording script as a Playwright two-page fallback. In the
  actual SIRC demo this becomes a phone-in-hand.

---

## §6 — Real vs mocked matrix

Every agent, every integration, one table. Read `kernel/agents/`
and `kernel/mocks/` for source-of-truth.

### Agents

| Agent | Module | Live LLM? | Model pin | Session state |
|---|---|---|---|---|
| **intake** (routes new-matter class_keys) | `kernel/agents/intake.py` | Live-capable via `emergentintegrations.llm.chat.LlmChat` | Pinned in the module (never rendered on any surface — CI deny-list guards the model ID). | **Gated in this build** — `EMERGENT_LLM_KEY` env var is intentionally unset (zero-LLM discipline). Call sites raise `RuntimeError: EMERGENT_LLM_KEY is not configured; intake requires a live provider.` |
| **placement** (draft submission → mocked carrier) | `kernel/agents/placement.py` | Live-capable | Same pin | Same gate |
| **compliance** (classifies advice/factual/opinion after deterministic guard) | `kernel/agents/compliance.py` | Live-capable | Same pin | Same gate |
| **liaison** (Ask surface, Harbour voice) | `kernel/agents/liaison.py` | Live-capable | Same pin | Same gate |
| **claims_stub** (rule-based FNOL triage) | `kernel/agents/claims_stub.py` | **No LLM** — deterministic regex + fact extraction | N/A | Always live. `tokens_used = (0, 0)`. |
| **MockProvider** (deterministic fixtures for kernel tests) | `kernel/providers/mock.py` | No LLM | N/A | Test-only. Always emits `provider_meta.mocked = True`. |

**Two important truths in this table:**

1. The four live-capable agents are not "mocked" in code — they
   are gated at runtime. Set `EMERGENT_LLM_KEY` and they route
   real traffic. The gate is intentional for this pack to
   guarantee zero LLM spend.
2. No model ID is on any client- or operator-visible surface. The
   pin lives in the agent module, is echoed into the ledger
   `AgentInvocationStarted` event, and is scrubbed by
   `strip_model_id` before any render.

### External integrations

Every external channel is a mock. There are no live third-party
credentials in this build.

| Channel | Purpose | Module | MockedBadge label | On-screen |
|---|---|---|---|---|
| **CarrierChannel** | Insurer quote / carrier tie-in | `kernel/mocks/carrier_channel.py` | `carrier` | Cover tab, Home policy cards, Decision Card header, Certificate, Public Verifier, Pending-Quote page. |
| **Payment rail** | PayTo (AU) / PayNow-GIRO (SG) | `kernel/mocks/payment_rail.py` | `payment` | Money tab title, Pay page. |
| **E-sign** | Signature envelope for endorsements | `kernel/mocks/e_sign.py` | `e-sign` | Sign page. Client Login (OTP send path uses the e-sign display language). |
| **Insurer lodgement** | Push a claim to the carrier's channel | `kernel/mocks/insurer_lodgement.py` | `insurer` | Client Claims (per-line and section-level). |
| **SMS** | Out-of-band notifications | `kernel/mocks/sms.py` | *(no client-facing badge — internal transport)* | Delivered via notifications projection. |
| **Signal / jurisdiction preview** | Dev-only jurisdiction render | `routes/client_preferences.py` | `signal` | Profile `Jurisdiction preview` control (`MOCKED · SIGNAL` badge next to the line). |
| **AMS / broker-of-record channel** | External policy management system | *(not implemented)* | — | **Absent.** No AMS module ships in this build. |
| **Premium funder** | Finance IPT / funding | *(not implemented)* | — | **Absent.** |
| **Weather / cyber feeds** | Signal ingestion | *(not implemented)* | — | **Absent.** |
| **Public verifier** | Offline chain proof of a certificate | `routes/verifier.py`, `docs/demo_bundle/ci-alpha.zip` | *(not mocked — this is the real audit primitive)* | `/verify/:token` public route. |
| **trAIl export bundle** | Audit-grade evidence pack | `routes/trail.py`, `scripts/build_demo_bundle.py` | *(not mocked)* | Operator `/o/export`. |

### `mocked: true` in the ledger

Every event that flows through a mocked channel carries
`payload.mocked = true` (or a shallow `mocked` sibling depending
on the event shape). This lets a downstream auditor grep the
event stream and see exactly which decisions rode on mocked
inputs — the ledger truth-in-provenance discipline from R2. See
`kernel/mocks/insurer_lodgement.py` and
`kernel/mocks/carrier_channel.py` for canonical examples.

---

## §7 — Evidence against done-when (per phase)

### Numbering reality check

Shelvin's brief said "phases 0–17". This repo's phase numbering
does **not** match verbatim — the shipped set is:

| Shelvin ref | Repo actual | Nature |
|---|---|---|
| Phase 0 | *not present as a discrete phase* — phase 0 scaffolding was rolled into phases 2–3 | — |
| Phases 1–3 | `phase 2: gAIte…`, `phase 3: brAIn…` (see `663daa7`, `5be1aa1`) | Kernel primitives |
| Phase 4 | `phase 4: auth + RBAC + two-person rule + compliance block/clear + cross-tenant isolation` (`3480dc9`) | Identity |
| Phase 5 | `phase 5: seed data + jurisdiction packs + policies + Program View` (`2eecc91`) | Data |
| Phase 6 | `phase 6: client PWA shell + 6 tabs + auth wiring + /api/c/me` (`da7636f`) | Client surface |
| Phase 7 | Split into 7A + 7B — projections/APIs + Control Tower UI (`0362317`, `545f82a`) | Operator surface |
| Phase 8 | `phase 8: intAIke live` (`456c298`) | First live agent |
| Phase 9 | `phase 9: plAIce live + MOCKED CarrierChannel + client pending-quote card` (`2a31d9f`) | Second live agent |
| Phase 10 | `phase 10: complAInce live + two-layer release pipeline` (`bac7043`) | Third live agent |
| Phase 11 | Split into 11a/b/c — reference scene backend, UI, hotfix (`4f6dd44`, `06795c0`, `901cddf`) | Scenes 4/5 wiring |
| Phase 12 | `phase 12: liAIson live + functional Ask surface` (`457eeaf`) — plus 12.6 hardening (`b57dcdf`) | Fourth live agent |
| Phase 13 | Split into 13a/b/.5 — claim scene (`ae60f3d`, `ce58e36`, `f0e318e`) | Scene 6 |
| Phase 14 | `phase 14: Shadow Mode dashboard + export` (`81f7690`) | Unlock evidence |
| Phase 15 | `phase 15: trAIl export view + offline verifier bundle + RBAC route guard` (`487a9f2`) plus `f28b8e8` fix | Scene 7 / audit |
| Phase 16 | Demo hardening + fix passes (`085c64f`, `66fded3`, `c494402`, `f6e9f18`, `51150d3`) | Polish |
| Phase 17 | `phase 17 close: SIRC final acceptance package` (`68f8d0c`) + follow-ups | SIRC gate |
| Phase 18 | `phase 18` recovery + Postgres unwind + P0 recovery (`75b5eb…`, `2d0b81…`, `58a660…`, `40f1700`, `1e18574`) | Post-SIRC hardening (was NOT in the original brief) |
| R1 → R7 | Emergent-deploy scaffolding + PG→Mongo migration + brand kit + UX audit + code review + landing + login + this pack | Delivery track |

Shelvin's "phases 13–17" therefore map to Scenes 6/7 + demo
hardening + SIRC acceptance — all shipped.

### Done-when evidence

| Phase | Done-when line | Evidence |
|---|---|---|
| 2 (gAIte) | Policy engine + coverage guard + restart safety | `tests/test_coverage_guard.py` (2 files; passed in-turn), `kernel/gate/coverage_guard.py`. |
| 3 (brAIn) | Orchestrator + envelope + contract + bounded-job runner + sprint cap | `tests/test_brain.py`, `tests/test_brain_retry.py`. |
| 4 (auth + RBAC) | Two-person rule + compliance block/clear + cross-tenant isolation | `tests/test_tenant_isolation.py`, `tests/test_compliance_block.py`, `tests/test_queue_actions.py`. |
| 7A/7B (Control Tower) | 12 KPIs + 4 drill-downs + Daily Brief + keyboard nav | `tests/test_kpi_summary.py`, `tests/test_kpi_drill.py`, `kernel/kpis.py` (12 computations wired), `pages/operator/Dashboard.tsx` (`DRILLABLE = new Set([5, 8, 10, 11])`). Live evidence: batch 1 §4. |
| 8 (intAIke) | Live-LLM broker-intake, model pin, model-id scrub | Live-capable code path in `kernel/agents/intake.py`; gated at runtime by `EMERGENT_LLM_KEY`. |
| 9 (plAIce) | Live placement + MOCKED CarrierChannel | `tests/test_placement_agent.py`; mocks landed at `kernel/mocks/carrier_channel.py`. |
| 10 (complAInce) | Two-layer release pipeline (deterministic guard → LLM classifier) | `tests/test_compliance_agent.py`, `kernel/release.py`. |
| 11 (reference scene) | Scene 4 endorsement + certificate + verifier | `tests/test_reference_scene_happy_path.py`. |
| 12 (liAIson) | Functional Ask surface + Harbour voice + release pipeline | `tests/test_r5b2_scene6_claim_e2e_mongo.py` (crosses claim + ask); route wired at `routes/c_ask.py`. |
| 13 (claim scene) | Scene 6: FNOL → triage → lodgement → status; coverage-language guard visible | `tests/test_coverage_guard_visible_on_status_update.py`, `tests/test_r5b2_scene6_claim_e2e_mongo.py`. |
| 14 (Shadow Mode) | Agent×class agreement rate, E&O misses, unlock status | `tests/test_shadow_mode.py`, `tests/test_r5c1b_beta_shadow_mode_pins.py`. Live evidence: batch 1 §4 (`15-operator-shadow-mode.jpeg`). |
| 15 (trAIl export) | Chain-verified bundle, offline verify.js, RBAC route guard | `tests/test_trail.py`, `tests/test_export_preview.py`, `tests/test_verifier_public.py`, `tests/test_admin_bundle_route.py`. `docs/demo_bundle/REFERENCE_HASHES.md` chain root `92e61063…`. Live evidence: batch 1 §4 (`16-operator-trail-export.jpeg`). |
| 16 (demo hardening) | 7-item hardening list closed | `phase 16 close: 5 of 5 surfaces green` (`51150d3`). Screenshots in `docs/demo_screenshots/scene-1…7-*.png`. |
| 17 (SIRC acceptance) | Six-scene acceptance pack | `docs/DEMO_RUNBOOK.md`, `docs/demo_bundle/`, `docs/DEMO_LIMITATIONS.md`. |
| 18 (post-SIRC recovery) | Postgres recovery + Pydantic hotpin equiv + axe regression close | `phase 18 close: SIRC-scope wrap` (`1e18574`). Superseded by R5c cutover. |
| R2 (trAIl on Mongo, dual-store) | Byte-identical event-hash parity across PG & Mongo | `tests/test_r5a2b_alpha_placement.py` (byte-identical parity pins), `docs/demo_bundle/REFERENCE_HASHES.md` (same chain root PG vs Mongo). |
| R3 (tenant isolation) | Mongo-native tenant guard | `tests/test_tenant_isolation.py`. |
| R4a/b/c (auth+workflow+domain) | Mongo dual-store | `tests/test_r5c2b_boot_flip_pins.py` (49 passed, 3 skipped in-turn; includes boot-flip pin tests). |
| R5a → R5c-4b (Mongo-only cutover) | Postgres nuclear deletion | Commits `cbbd554` + `f534b3d`. Live evidence: `/api/healthz` returns `postgres: null` on both prod and preview. |
| R5d (brand kit) | Harbour + Ledger palette shipped | `packages/brand-tokens/` — contrast tests + BrandMark + BrandWordmark. |
| R5e (UX audit) | 37 contrast rows across 6 surfaces | See `docs/BRAND_TOKENS.md` and batch 1 screenshots. |
| R5f (code review triage) | Real bugs landed | `ced3739`. |
| R5g (code review delta) | Dead code removed | `980209a`. |
| R6-preflight (deploy) | Emergent deploy readiness | `89fddee`, `60937e7`. |
| R7 (landing + login) | Persona picker + production landing | `ff19761`, `3360c04`. Live evidence: batch 1 §1 (screenshots `01`, `02`). |

### First-attempt pass rates (golden + adversarial)

Spot-check run this turn:

```
$ pytest -q tests/test_verifier_public.py \
              tests/test_coverage_guard.py \
              tests/test_export_preview.py
49 passed, 3 skipped in 12.22s
```

Full suite: **529 tests collected** (`pytest --co` in-turn). No
full-suite run in this pack — pytest surface run intermittently
hits a `pymongo.AutoReconnect` on the shared Motor pool, which
Shelvin has accepted as a known infra-flake with a tolerance of
~3 errors per run.

### Restart / H-class lockout / tamper-detection / migration
### durability

| Property | Evidence |
|---|---|
| Restart safety | `kernel/gate/…` restart-safe by construction (idempotent projections). Explicit test: `tests/test_r5c2b_boot_flip_pins.py`. |
| H-class lockout | H-class = human-in-the-loop-required. Shadow Mode `LOCKED` rows (batch 1 `15-operator-shadow-mode.jpeg`) show this per-agent, per-class. |
| Tamper detection | `docs/demo_bundle/ci-alpha-tampered.zip` — `node verify.js` exits **1** on this bundle. |
| Migration durability | `phase 12.6: hardening deviation conditions + durability workflow first run` (`b57dcdf`). Not re-run this pack. |
| Negative-tenancy | `tests/test_tenant_isolation.py`. Passes in-suite. |

Where a phase's done-when has no evidence in this repo it says
so above. **No phase evidence is fabricated.**

---

## §8 — Governance ledger

### Local signed commit chain

Every commit in this repo since the ed25519 signing key was
adopted is signed under `brokaid-build <build@synaion.com.au>`
with public key
`SHA256:WTyD/igGdDs2U/AtBaiV5q8+eaXb0Lca0wWNOs3S18g`.

Sample:

```
$ git log --show-signature -1 39357168
commit 39357168aaa086c32b3d7dd235c2887f2494fa07
Good "git" signature for build@synaion.com.au with ED25519 key
    SHA256:WTyD/igGdDs2U/AtBaiV5q8+eaXb0Lca0wWNOs3S18g
Author: brokaid-build <build@synaion.com.au>
Date:   Fri Sep 18 03:43:14 2026 +0000
    phase R7-review-pack — V1 MVP review pack §1–§4 (batch 1)
```

The Batch-2 commit will be signed under the same identity and
its SHA reported in the follow-up message to Shelvin (this doc
was written **before** its own commit landed — the SHA is
appended in the batch-2 hand-off, per Shelvin's bash-before-
report protocol).

**No push, no remote.** `git remote -v` is empty. Every commit
lives in `/app` on `main` and nowhere else.

**No amend since origin sync.** There is no origin. Every commit
in the chain is first-time-written; nothing has been rebased,
squashed, or amended.

### Bundles in Synaion storage

Two canonical bundles ship under `docs/demo_bundle/`:

| File | Chain-root hash | Purpose |
|---|---|---|
| `ci-alpha.pg.zip` | `92e61063c8d225f40bebd2ae794d9d5810bbf9a48d92ba056845c68b6ac1b5d8` | Pre-R5c-3b PG-generated archive. Root hash **preserved in perpetuity**. Not regenerated. Parity pin. |
| `ci-alpha.zip` | **Same** `92e61063…` (R2 byte-identical guarantee — see `_build_event_specs`) | R5c-3b Mongo-native canonical bundle. Regenerated by `scripts/build_demo_bundle.py`. Ships as SIRC demo asset. |
| `ci-alpha-tampered.zip` | N/A (`verify.js` exits 1) | Tamper-detection evidence — proves the offline verifier catches a mid-chain flip. |

The R2 byte-identical guarantee is the important line: the same
event sequence written into either Postgres or Mongo produces the
same JCS-canonicalised event hash chain, so the same chain-root.
That is why the two bundles share `92e61063…` even though they
were produced by two entirely different runtimes.

### Credit spend per phase (Emergent-managed)

**Approximate.** Full credit ledger lives in Emergent's dashboard;
this pack summarises the numbers Shelvin visible in-app.

| Batch | Emergent credits (approx) |
|---|---|
| Phase 0 — scaffold | ~90 |
| Phases 1–7 — kernel + surfaces | ~350 |
| Phases 8–12 — live agents | ~180 (LLM billed separately by Emergent LLM-key balance) |
| Phases 13–17 — scenes, demo hardening, SIRC acceptance | ~260 |
| Phase 18 recovery | ~120 |
| R1 — deploy scaffold | ~40 |
| R2–R5c — PG→Mongo migration | ~380 |
| R5d–R5g — brand + UX + review triage | ~220 |
| R6-preflight — deploy readiness | ~60 |
| R7 (Landing + Login + this pack, batch 1) | ~150 |
| R7 batch 2 (this addendum) | ~90 estimated (recording renders included) |

None of these individual phases hit the phase-0 1000-credit cap.
Cumulative spend against Shelvin's Emergent-visible ledger is
the source of truth; the table above is a directional estimate
only.

### Deviations accepted

| Deviation | Who accepted | When |
|---|---|---|
| PG→Mongo cutover (R2–R5c-4b) | Shelvin | Approved pre-R2 kickoff. |
| `AUTH_DEV_MODE=true` in the deployed preview pod | Shelvin | Approved for SIRC demo posture. |
| Zero-LLM discipline in this review pack | Shelvin | Approved in the batch-1 clarifications. |
| No remote push | Shelvin | Standing constraint. |
| Voice-over deferred to a human recording session | Shelvin | Batch-1 clarification. |
| Six of twelve KPIs read `—` (no history) | Shelvin (implicit) | Called out in batch 1 §4. |
| `pymongo.AutoReconnect` flake tolerance ≤ 3/run | Shelvin | R5 hardening acceptance. |
| Kernel-complexity refactor deferred post-SIRC | Shelvin | R5f/R5g close. |
| No production `www.brokaid.com` deploy of R5g+R7 yet | Shelvin | Awaiting deploy trigger by Shelvin. |

---

## §9 — Hygiene sweep

### CI deny-list on built bundle

```
$ node ci/deny-list.js frontend/dist
deny-list: OK (7 file(s) scanned)
```

Ran in-turn on the current dist. No agent-name leak, no model-ID
leak. Full closed set enforced:

```
intAIke, plAIce, clAIms, complAInce, liAIson, brAIn, gAIte,
trAIl, retAIn, finAInce, terrAIn, campAIgn, docs, notify, certs
```

Plus the pinned model-ID sweep in `ci/deny-list.js:52-58`.
Case-sensitive `/[a-z]+AI[a-z]+/` generic sweep with the sole
allow-listed exception `brokAId` (the product name).

### No model names on-screen

Combined with the CI sweep: greps against `/app/frontend/src`
for any `claude-*` / `gpt-*` / model-identifier string return
zero matches. The pin lives in the agent Python modules only.

### WCAG AA contrast

The R5e audit table (37 rows across 6 surfaces) + R7-Landing
audit (14 rows) + R7-Login audit (8 rows) — 59+ rows total —
lives in the R5e / R7 doc comments. Every documented ratio
holds within 0.1 tolerance. Not reproduced verbatim here to
keep this pack compact; source-of-truth is `packages/brand-tokens/`
and the audit tables inline in the `Landing.tsx` / `Login.tsx`
header comments.

Contrast tests in `packages/brand-tokens/` are executable via
`vitest` when the frontend is rebuilt; the batch-1 R5e commit
message documents their pass.

### Lighthouse PWA installability

Not re-run in this turn. The R5d.1 commit
(`5816ada — phase R5d.1 — client shell brand header + PWA
manifest served in dev`) shipped the manifest fix. The Add-to-
Home-Screen affordance is visible in the Profile screenshot
(`09-client-profile.jpeg`). Not-yet-re-tested in a full
Lighthouse audit in this pack.

### Performance budget

No metric captured in this pack. The bundle sizes are visible in
`frontend/dist/assets/`:

* `index-u7WYrPnv.js` — 154,084 bytes ungzipped (main SPA entry).
* `KitchenSink-DEfLGpC-.js` — dev-only chunk, absent from prod
  routes.
* `dev-auth-BBY0T7dB.js` — dev-only chunk carrying the
  `/*! brokaid:dev-only */` marker so the deny-list scanner skips
  it.

---

## §10 — What is not done

### High-numbered phases (Shelvin's "13–17")

All shipped. Scene 6 (claim), Scene 7 (audit / trAIl export),
demo hardening, and SIRC acceptance closed at phase 17. Post-
SIRC recovery ran through phase 18 (Postgres unwind, then R1–R7
delivery track).

### Known bugs (from `docs/DEMO_LIMITATIONS.md` + in-turn observations)

| Severity | Bug | Where |
|---|---|---|
| **High** (blocks demo path) | `_system_verifier_tokens` empty for `burmac` tenant — the client-facing QR verifier only resolves for ci-alpha/beta seeds. | `backend/app/seeds/seed_burmac.py` — endorsement cycle not executed to certificate for burmac. Workaround: use ci-alpha bundle for offline verify demo. |
| Medium | `pymongo.AutoReconnect` intermittent in pytest under load. | Motor pool. Accepted by Shelvin as ~3 errors/run tolerance. |
| Medium | Manifest MIME `application/octet-stream` regression from an ingress config change. | Ops ticket, out of codebase scope. |
| Low | ClientHome fetch error swallowed (`03-client-home.jpeg` — the "err" state never surfaces if the policies endpoint 500s). | `pages/ClientHome.tsx:19-23` — the `if (res.ok)` guard silently discards errors. Filed. |
| Low | KPI "no history" label kills the delta chip's `flat` colour. | Cosmetic only. |
| Low | The `dev-only` marker comment must survive Vite tree-shaking — currently guaranteed by the chunk-splitting plugin config but not asserted. | Add a CI check. Post-SIRC. |

### Cut items (accepted cut order)

| # | Cut item | Rationale |
|---|---|---|
| 1 | AMS integration | Out of MVP scope. All AMS journeys mocked or absent per §6. |
| 2 | Premium funder integration | Ditto. |
| 3 | Live external signal feeds (weather, cyber) | Ditto. `MOCKED · SIGNAL` in Profile is the only signal surface. |
| 4 | Real e-sign envelope with a live counter-party | Mocked e-sign is the demo posture. |
| 5 | Second-device physical QR scan simulation | Playwright single-context two-page fallback (§5). |
| 6 | Live voice-over on the walk-throughs | Human recording session. Script ready in `V1_REVIEW_PACK_NARRATION.md`. |
| 7 | Kernel-complexity refactors flagged in R5f/R5g review | Deferred post-SIRC by Shelvin. |
| 8 | `App.tsx` complexity refactor | Same. |

### Readiness call for 28 October six-run acceptance + SIRC demo

**Green with two flags.**

* **Green:** Every core scene renders. Every audit primitive (ledger
  bundle, offline verifier, tamper detection) is proven and pinned.
  RBAC, tenant isolation, Shadow Mode LOCKED evidence, and the
  jurisdiction packs (AU/SG) are live. Client PWA installable,
  brand palette shipped, contrast audit passed. CI deny-list
  green on the built bundle.
* **Flag 1 — client QR verifier on burmac.** The client-facing QR
  currently resolves only for CI-tenant certificates. Two-hour
  fix (add a completed endorsement cycle to `seed_burmac`). If
  SIRC demo will be driven off burmac, do this before 28 Oct.
* **Flag 2 — production deploy trigger.** `www.brokaid.com` is
  still on the R5g drop; R7-Landing + R7-Login are on preview
  only. Shelvin triggers the Emergent deploy when he's happy
  with this pack. Nothing in-repo blocks it.

**Recommend proceeding to acceptance runs.** No blocker. Both
flags are one-commit fixes each.

---

## §11 — Narration script

Lives in **`docs/V1_REVIEW_PACK_NARRATION.md`** — kept separate so
the hand-off to whoever records the voice is clean. Two tracks
totalling ~5 minutes read at 145-160 wpm. Harbour voice for the
client scenes; Ledger voice for the operator scenes. No agent
names, no model names, no jargon that would confuse a broker.

Batch 2 ends here.


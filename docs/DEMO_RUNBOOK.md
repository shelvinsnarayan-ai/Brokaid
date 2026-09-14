# brokAId — SIRC Demo Runbook

**Format:** printable one-pager. Two tabs open in parallel:

- **Tab A (Client) — Harbour palette** — `https://<host>/c/`
- **Tab B (Operator) — Ledger palette** — `https://<host>/o/`

Impersonation shortcut (dev only, `AUTH_DEV_MODE=true`):
`POST /api/auth/dev/impersonate {"email": "..."}`. Operator seat used
throughout: `principal@burmac.example` (Ada Principal). Client seat:
`owner@fictionalclient.example` (Kai Owner).

> **All external systems are mocked.** Every mocked action shows a
> `MOCKED · <label>` pill on screen and its ledger event carries
> `mocked: true`.

---

| # | Scene | Persona / Tab | URL | Click path (compressed) | Expected on-screen proof | Talking-point cue |
|---|---|---|---|---|---|---|
| 1 | Intake + triage | Ada Principal · Tab B | `/o/queue` | Land on Queue → press `j` to move → press `Enter` on the top pending → read the triage card | `queue-page` renders; SLA chips, confidence bar, `Route` cell shows role; `MOCKED · e-sign` pill visible if envelope-touched | "One list. Deterministic triage. No inbox, no folders — the queue *is* the work." |
| 2 | Placement via plAIce | Ada · Tab B | `/o/queue/:envelope_id` | Approve on Decision Card → click **View trail** on the timeline | Decision Card three-col; timeline row `AgentInvocationCompleted` with `attempt_count`; `MOCKED · carrier` pill on the quoted row | "Placement is an agent — but every step is a ledger event before the client ever sees it." |
| 3 | Compliance block → deterministic rewrite → release | Ada + Fin Compliance · Tab B, then Kai · Tab A | `/o/queue` → `/c/cover/:policy_id/pending` | Compliance blocks a client message → cache-hit rewrite is offered → operator releases → client sees the corrected text | Ledger rows: `ClientMessageRewriteRequired` → `ClientMessageReleased`. Rewrite pill labels the cache-hit. Client tab shows only the released copy | "Blocks are learned, not blocking. The rewrite is cached and deterministic — no LLM at demo time." |
| 4 | Issue policy + real QR-on-cert | Ada · Tab B; Kai · Tab A | `/o/queue` approve → `/c/cover/:policy_id/certificate/:envelope_id` | Approve issuance in Tab B; refresh Tab A cover page | Tab A cover page renders certificate; `<img src=".../qr.png">` is a real scannable QR; `MOCKED · insurer` pill on issuance row | "The QR resolves to `/verify/:token` — pull out your phone, it works offline of us." |
| 5 | Endorsement scene end-to-end | Kai + Ada · Tabs A and B alternating | `/c/cover/:policy_id/request-change` → `/o/queue` → `/c/cover/:policy_id/pay/:envelope_id` → `/c/cover/:policy_id/sign/:envelope_id` → `/c/cover/:policy_id/certificate/:envelope_id` | Client requests change → operator approves → client pays (mocked) → client signs (mocked) → new cert renders | `MOCKED · payment` and `MOCKED · e-sign` pills; ledger trail spans `ClientChangeRequested` → `EndorsementQuoteReady` → `PaymentReceived` → `SignatureCompleted` → `EndorsementExecuted` → `CertificateRegenerated`; every step is a ledger event | "One flow, twelve events, both audiences agree on the truth — because they read the same ledger." |
| 6 | Claim scene (FNOL → lodgement → notification) | Kai · Tab A; Ada · Tab B | `/c/claims/new` → `/o/claims/:claim_id` → `/c/notifications` | Client files FNOL → operator lodges to insurer → notification returns to client | Tab B claim detail shows lodgement row with `MOCKED · insurer`; Tab A notifications list gains one row with `MOCKED · email` (files at `/tmp/brokaid_emails/`); ledger `ClaimLodged` + `Notification` events | "The client sees the same status the insurer sends us. One channel, mocked, but wired end-to-end." |
| 7 | **trAIl export + offline tamper-verify** | Ada · Tab B; laptop terminal | `/o/export` → chip `Last 30 days` → **Download ledger export bundle** | Save `brokaid-ledger-export-...zip` locally → `unzip` → `npm i canonicalize && node verify.js export.json` | Console prints `OK — <n> events verified, tenant burmac, root <hash>`; then `unzip ci-alpha-tampered.zip && node verify.js export.json` prints `FAIL — event #<seq>` | See callout below. |

---

> **⚡ The wow beat (scene 7) — live-verify the ledger in front of the room, 5 seconds.**
>
> Point at the terminal.
> 1. `node verify.js export.json` → **`OK — N events verified`**.
> 2. Switch to the pre-tampered bundle: `node verify.js tampered/export.json` → **`FAIL — event #<N>`**.
>
> This is the whole thesis in ten characters of terminal output. The
> verifier is a 130-line file, no server, no login, no trust — anyone
> in the room can rerun it on their own laptop. That's what "the client
> owns their own audit" means. Do not skip this step.

---

**Persona choreography (if you deviate, tell the room first):**
Ada owns the operator tab across every scene except the FNOL step in scene 6
(client-initiated) and the `request-change` step in scene 5 (client-initiated).
Fin Compliance only surfaces in scene 3 (block+rewrite). Kai owns the client
tab; Sam Finance is untouched in this run.

**Reset-between-runs:** In dev, hit `POST /api/dev/reset` (if mounted) or
re-seed via `python -m services.api.app.seeds.seed_all`. Everything is
fictional.

**Referenced screenshots:** see `docs/demo_screenshots/scene-{1..7}.png`.

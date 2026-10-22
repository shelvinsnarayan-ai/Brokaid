# brokAId — SIRC Demo Runbook

**Format:** printable one-pager. Two tabs open in parallel:

- **Tab A (Client) — Harbour palette** — `https://<host>/c/`
- **Tab B (Operator) — Ledger palette** — `https://<host>/o/`

Impersonation shortcut (dev only, `AUTH_DEV_MODE=true`):
`POST /api/auth/dev/impersonate {"email": "..."}`. Three operator seats
rotate across the run so the audience sees a role split, not one hero:

- **Ada Principal** — `principal@burmac.example` — scenes 1, 3, 7
- **Cass Executive (AE)** — `ae@burmac.example` — scenes 2, 5
- **Fin Compliance** — `compliance@burmac.example` — walks on for scene 3 only

Client seat throughout: **Kai Owner** — `owner@fictionalclient.example`
(scenes 4 and 6 client-initiated; scenes 3 and 5 client-receiving).

> **All external systems are mocked.** Every mocked action shows a
> `MOCKED · <label>` pill on screen and its ledger event carries
> `mocked: true`.

> **Before each demo (Harbourline client app):** run
> `cd backend && python -m app.scripts.demo_refresh_unread`. Anyone who opened the claim thread earlier (rehearsals, testers)
> will have marked it read. This puts back exactly one unread broker reply on CLM-2026-000013, so Messages and the claim
> tracker show the "New" highlight again. It is safe to run any number of times and writes a ledger row only when it changes something.

---

| # | Scene | Persona / Tab | URL | Click path (compressed) | Expected on-screen proof | Talking-point cue |
|---|---|---|---|---|---|---|
| 1 | Intake + triage | Ada Principal · Tab B | `/o/queue` | Land on Queue → press `j` to move → press `Enter` on the top pending → read the triage card | `queue-page` renders; SLA chips, confidence bar, `Route` cell shows role; `MOCKED · e-sign` pill visible if envelope-touched | "One list. Deterministic triage. No inbox, no folders — the queue *is* the work." |
| 2 | Placement via plAIce | **Cass Executive · Tab B** | `/o/queue/:envelope_id` | Impersonate as `ae@burmac.example` → approve on Decision Card → click **View trail** on the timeline | Decision Card three-col; timeline row `AgentInvocationCompleted` with `attempt_count`; `MOCKED · carrier` pill on the quoted row; the AE seat is the approving role, not the principal | "Placement is what the AE seat spends its day on — but every step is a ledger event before the client ever sees it." |
| 3 | **complAInce block → deterministic rewrite → release** | Ada Principal + Fin Compliance · Tab B, then Kai · Tab A | `/o/queue` → `/c/cover/:policy_id/pending` | Compliance blocks a client message → cache-hit rewrite is offered → operator releases → client sees the corrected text | Ledger rows: `ClientMessageRewriteRequired` → `ClientMessageReleased`. Rewrite pill labels the cache-hit. Client tab shows only the released copy | See callout below. |
| 4 | Issue policy + real QR-on-cert | Ada · Tab B; Kai · Tab A | `/o/queue` approve → `/c/cover/:policy_id/certificate/:envelope_id` | Approve issuance in Tab B; refresh Tab A cover page | Tab A cover page renders certificate; `<img src=".../qr.png">` is a real scannable QR; `MOCKED · insurer` pill on issuance row | "The QR resolves to `/verify/:token` — pull out your phone, it works offline of us." |
| 5 | Endorsement scene end-to-end | **Cass Executive · Tab B** + Kai · Tab A | `/c/cover/:policy_id/request-change` → `/o/queue` → `/c/cover/:policy_id/pay/:envelope_id` → `/c/cover/:policy_id/sign/:envelope_id` → `/c/cover/:policy_id/certificate/:envelope_id` | Kai requests change → Cass (AE) approves the endorsement → Kai pays (mocked) → Kai signs (mocked) → new cert renders | `MOCKED · payment` and `MOCKED · e-sign` pills; ledger trail spans `ClientChangeRequested` → `EndorsementQuoteReady` → `PaymentReceived` → `SignatureCompleted` → `EndorsementExecuted` → `CertificateRegenerated`; every step is a ledger event; the approver is the AE, not the principal | "Routine endorsements route to the AE, not the principal — showing the desk actually delegates." |
| 6 | Claim scene (FNOL → lodgement → notification) | Kai · Tab A; Ada · Tab B | `/c/claims/new` → `/o/claims/:claim_id` → `/c/notifications` | Client files FNOL → operator lodges to insurer → notification returns to client | Tab B claim detail shows lodgement row with `MOCKED · insurer`; Tab A notifications list gains one row with `MOCKED · email` (files at `/tmp/brokaid_emails/`); ledger `ClaimLodged` + `Notification` events | "The client sees the same status the insurer sends us. One channel, mocked, but wired end-to-end." |
| 7 | trAIl export + offline verify (closing receipt) | Ada · Tab B; laptop terminal | `/o/export` → chip `Last 30 days` → **Download ledger export bundle** | Save `brokaid-ledger-export-...zip` locally → `unzip` → `npm i canonicalize && node verify.js export.json` | Console prints `OK — <n> events verified, tenant burmac, root <hash>`; then `unzip ci-alpha-tampered.zip && node verify.js export.json` prints `FAIL — event #<seq>` | See the closing-receipt framing below. |

---

> **⚡ The live moment (scene 3) — pause here.**
>
> complAInce blocks the release for a legitimate policy reason
> (`non_standard_wording` or `coverage_language_detected`), and the
> deterministic guard proposes a compliant rewrite in one click. No
> LLM runs at demo time — the rewrite comes out of the cache Root
> approved in Phase 16. This is the AI-with-guardrails beat.
>
> Point at three things, in order:
> 1. The block row on the timeline — `ClientMessageRewriteRequired`.
>    Governance kept the original, it didn't delete it.
> 2. The rewrite pill — labelled cache-hit, not model-generated.
> 3. The client's `/c/cover/:policy_id/pending` — Kai sees only the
>    compliant copy. The unsafe version never left the ledger.
>
> If you skip anything, don't skip this. It's what "AI with a leash
> the compliance officer holds" actually looks like.

---

**Scene 7 — closing receipt, not crescendo.**
Scene 7 is now the *proof* that the six preceding scenes are honest,
not the peak of the demo itself. It's short: one clean verify, one
tampered verify, ten characters of terminal output. The whole session
is provable offline in 5 seconds:

1. `node verify.js export.json` → **`OK — N events verified`**.
2. `node verify.js tampered/export.json` → **`FAIL — event #<N>`**.

The verifier is a 130-line file, no server, no login, no trust —
anyone in the room can rerun it on their own laptop. Treat it as the
receipt the audience walks out with, not the punchline.

---

**Persona choreography (if you deviate, tell the room first):**
Ada Principal owns scenes 1, 3 (with Fin Compliance) and 7 — the strategic,
compliance, and receipt moments. Cass Executive (AE) drives scenes 2 and 5
— placement and endorsement, the AE's actual day-to-day. Kai owns the
client tab and initiates in scenes 4 and 6. Sam Finance is untouched
in this run.

**Reset-between-runs:** In dev, hit `POST /api/dev/reset` (if mounted) or
re-seed via `python -m services.api.app.seeds.seed_all`. Everything is
fictional.

**Referenced screenshots:** see `docs/demo_screenshots/scene-{1..7}-*.png`.
Scenes 2 and 5 are recorded under a live Cass Executive session — the
operator user header in each shot reads `Cass Executive`, not
`Ada Principal`.

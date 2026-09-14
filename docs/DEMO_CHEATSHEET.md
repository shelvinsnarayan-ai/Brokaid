# brokAId — SIRC Demo Cheatsheet

Half-page. Bullets only. Pin this beside the runbook.

## MOCKED badges to name-check

- **`MOCKED · carrier`** — CarrierChannel quote/bind steps in scenes 1, 2, 5.
- **`MOCKED · payment`** — payment mandate + capture in scene 5 (client `/c/cover/:policy_id/pay/:envelope_id`).
- **`MOCKED · e-sign`** — signature request + completion in scene 5 (client `/c/cover/:policy_id/sign/:envelope_id`).
- **`MOCKED · email`** — outbound notifications land on disk at `/tmp/brokaid_emails/*.eml`, visible on client `/c/notifications` (scene 6).
- Bonus: **`MOCKED · insurer`** on claim lodgement (scene 6) and **`MOCKED · ams`** on any AMS-sync row that surfaces.

## Where the append-only guarantee shows up in the UI

- Any ledger row on `/o/queue/:envelope_id` **Timeline** column — timestamps are monotonic, event types are immutable, no edit-in-place UI ever.
- The **BuildIndicator** in the operator shell footer (`build 51150d3 · G`) is baked at build time, not fetched — a runtime API cannot mutate that stamp.
- `POST /api/o/compliance/block` **does not delete** the offending message; it emits a `ComplianceBlockAdded` event. Point at the two rows in the timeline (block + rewrite) — the original is preserved by design.
- `PUT`s that logically "cancel" (e.g. `ClaimStatusUpdate` moving to `withdrawn`) are always additive events, never destructive.

## Where the LLM traces are visible in `/o/queue`

- Decision Card three-column view on `/o/queue/:envelope_id` — **Evidence** column shows the classifier evidence rows.
- **Timeline** column shows `AgentInvocationCompleted` events. Payload fields to point at: `agent_name`, `attempt_count`, `acceptance_mode`, `mocked` (should be `false` in acceptance mode — deterministic path only).
- Model IDs are **never** rendered on screen — that's a governance rule, not a UX omission.

## RBAC-negative demonstration

- Open **Tab A** (client) DevTools → Network.
- In Tab A URL bar, type `/o/export`.
- Fetch fails with **HTTP 403**. React app shows a "not authorised" fallback (no crash).
- Then in Tab B (operator, `principal@burmac.example`) navigate to `/o/export` → 200 + full page.
- Same URL, same host, different role → different outcome. Point at the network panel.

## Retry telemetry (scene 2 or scene 5)

- On `/o/export`, click chip **Last 7 days** → **Preview event count**.
- In the preview panel, scroll the type table to `AgentInvocationCompleted`.
- Open one bundle export and grep the JSON for `attempt_count`:
  `zcat export.json | jq '.events[] | select(.type=="AgentInvocationCompleted") | .payload.attempt_count'`.
- Acceptance mode caps this to `1`. Point at that cap — no infinite retry loop is possible.

## Emergency toggles (don't touch unless asked)

- `AUTH_DEV_MODE=false` → `/dev/login` disappears, banner disappears, impersonation route 404s. Do NOT flip mid-demo.
- `BROKAID_ACCEPTANCE_MODE=true` → LLM retry cap = 1. Already set in demo env.

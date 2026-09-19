# brokAId — SIRC Demo Cheatsheet

Half-page. Bullets only. Pin this beside the runbook.

## Persona rotation (dev-impersonate, `AUTH_DEV_MODE=true`)

- **Ada Principal** — `principal@burmac.example` — scenes 1, 3, 7.
- **Cass Executive (AE)** — `ae@burmac.example` — scenes 2, 5. RBAC-confirmed on `o_placement` + `/o/queue` approve.
- **Fin Compliance** — `compliance@burmac.example` — scene 3 only.
- **Kai Owner** — `owner@fictionalclient.example` — client tab across all scenes.

`POST /api/auth/dev/impersonate {"email": "..."}`. Route is mounted only under `AUTH_DEV_MODE=true` and gated by the same `settings.assert_safe_to_start()` guard as the DEV banner.

## Where to point in scene 3 (the live moment)

- Timeline on `/o/queue/:envelope_id` — `ClientMessageRewriteRequired` row lives right beside the original. The block preserves, doesn't delete.
- The rewrite chip on the operator tab — reads "cache" or similar, not "model". Deterministic guard, not LLM at demo time.
- Client tab `/c/cover/:policy_id/pending` — Kai sees only the compliant copy. The unsafe wording never crossed the ledger.
- If someone asks "did an LLM run": no. `BROKAID_ACCEPTANCE_MODE=true` caps retries at 1 and the guard-rewrite cache serves the corrected wording directly.

## MOCKED badges to name-check

- **`MOCKED · carrier`** — CarrierChannel quote/bind in scenes 1, 2, 5.
- **`MOCKED · payment`** — payment mandate + capture in scene 5.
- **`MOCKED · e-sign`** — signature request + completion in scene 5.
- **`MOCKED · email`** — outbound notifications on disk at `/tmp/brokaid_emails/*.eml`, visible on `/c/notifications` (scene 6).
- Bonus: **`MOCKED · insurer`** on claim lodgement (scene 6) and **`MOCKED · ams`** on any AMS-sync row that surfaces.

## Where the append-only guarantee shows up in the UI

- Any ledger row on `/o/queue/:envelope_id` **Timeline** column — timestamps monotonic, event types immutable, no edit-in-place UI ever.
- The **BuildIndicator** in the operator shell footer (`build 51150d3 · G`) is baked at build time via Vite `define`, not fetched — a runtime API cannot mutate it.
- `POST /api/o/compliance/block` **does not delete** the offending message; it emits a `ComplianceBlockAdded` event. Point at the two rows in the timeline (block + rewrite) — the original is preserved by design.
- `PUT`s that logically "cancel" (e.g. `ClaimStatusUpdate → withdrawn`) are always additive events, never destructive.

## Where the LLM traces are visible in `/o/queue`

- Decision Card three-column view on `/o/queue/:envelope_id` — **Evidence** column shows the classifier evidence rows.
- **Timeline** column shows `AgentInvocationCompleted` events. Payload fields to point at: `agent_name`, `attempt_count` (≤1 in acceptance mode), `acceptance_mode`, `mocked`.
- Model IDs are **never** rendered on screen — governance rule, not UX oversight.

## RBAC-negative demonstration

- Open **Tab A** (client) DevTools → Network.
- In Tab A URL bar, type `/o/export`.
- Fetch fails with **HTTP 403**. React app shows a "not authorised" fallback (no crash).
- Then in Tab B (operator, `principal@burmac.example`) navigate to `/o/export` → 200 + full page.
- Same URL, same host, different role → different outcome. Point at the network panel.

## RBAC-negative moment · auditor cannot log in (add a screenshot)

Data-only roles cannot even authenticate. Exact click-path for the demo, in this order:

1. From an operator tab with DevTools **Network** open, clear the filter, keep only `Fetch/XHR`.
2. Paste into DevTools **Console** (do NOT navigate the URL bar):
   ```js
   fetch("/api/auth/dev/impersonate", {
     method: "POST",
     headers: {"Content-Type": "application/json"},
     body: JSON.stringify({email: "auditor@burmac.example"}),
   }).then(r => r.json().then(b => console.log(r.status, b)));
   ```
3. Point at the **403** row in the Network panel. Click it → **Response** tab. Body reads:
   ```json
   {"detail": "no_login_capable_role"}
   ```
4. Screenshot at `docs/demo_screenshots/rbac-negative-auditor.png` captures this exact 403 + response body.

Locked by `backend/tests/test_rbac_auditor_impersonate.py::test_auditor_impersonate_returns_403` — the test hits the same endpoint with the same email and asserts `status == 403` + `body == {"detail": "no_login_capable_role"}`. Regression-guarded by the static assertion that `"auditor" in DATA_ONLY_ROLES and "auditor" not in LOGIN_CAPABLE_ROLES` in the same file.

## Retry telemetry (scene 2 or scene 5)

- On `/o/export`, click chip **Last 7 days** → **Preview event count**.
- In the preview panel, scroll the type table to `AgentInvocationCompleted`.
- Open one bundle export and grep the JSON for `attempt_count`:
  `zcat export.json | jq '.events[] | select(.type=="AgentInvocationCompleted") | .payload.attempt_count'`.
- Acceptance mode caps this to `1`. Point at that cap — no infinite retry loop is possible.

## Emergency toggles (don't touch unless asked)

- `AUTH_DEV_MODE=false` → `/dev/login` disappears, banner disappears, `dev/impersonate` route 404s. Do NOT flip mid-demo.
- `BROKAID_ACCEPTANCE_MODE=true` → LLM retry cap = 1. Already set in demo env.

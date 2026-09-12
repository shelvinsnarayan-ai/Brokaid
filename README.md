# brokAId

Agentic OS for insurance brokers. Property of **Synaion Pty Ltd**.

This is the monorepo. Phase 0 lands foundations: brand tokens, MOCKED
primitive, CI deny-list, dev-mode auth scaffold, empty kernel, two route
shells (client + operator), FastAPI service with Postgres wire-up.

## Layout

```
apps/web            # Vite + React + TS single app (client `/c/*` + operator `/o/*`)
services/api        # FastAPI service (Postgres)
packages/kernel     # empty scaffold — types land in phases 1-3
packages/brand-tokens # Harbour + Ledger palettes, typography, contrast tests
packages/mocks      # MockedBadge + placeholder mock adapters
infra/postgres      # local Postgres compose / init scripts
ci/                 # deny-list scanner
docs/               # governance, brand tokens, seeds discipline
```

## Governance

See `docs/GOVERNANCE.md`. Twelve ground rules; the CI deny-list job
enforces rule 6. Never bypass.

## Development

- Frontend: Vite dev server on port 3000 (via `yarn workspace @brokaid/web start`).
- Backend: FastAPI on port 8001 (via supervisor).
- Postgres: local cluster on `localhost:5432`, database `brokaid`.

## Seeds

No real client data, ever. See `docs/SEEDS_ARE_FICTIONAL.md`.

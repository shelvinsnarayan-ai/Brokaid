# brokAId

Agentic OS for insurance brokers. Property of **Synaion Pty Ltd**.

Runtime is **MongoDB-only** as of Phase R5c-4b (2026-09-16). Postgres,
`asyncpg`, and `psycopg2-binary` have been removed from the runtime and
from `backend/requirements.txt`. The append-only trAIl ledger,
tenant-scoped indices, and every projection now live in Mongo behind
`app.db.tenant_safe.TenantSafeDatabase`. A tenant context is required
on every access and is bound per-request by
`app.middleware.tenant.TenantContextMiddleware`.

## Layout

```
frontend             # Vite + React + TS single app (client `/c/*` + operator `/o/*`)
backend              # FastAPI service (Mongo-only, Motor async driver)
packages/kernel      # empty scaffold — types land in phases 1-3
packages/brand-tokens # Harbour + Ledger palettes, typography, contrast tests
packages/mocks       # MockedBadge + placeholder mock adapters
ci/                  # deny-list + Mongo write / raw-collection / ts_override scanners
docs/                # governance, brand tokens, seeds discipline
```

## Governance

See `docs/GOVERNANCE.md`. Twelve ground rules; the CI deny-list job
enforces rule 6. Never bypass.

## Development

- Frontend: Vite dev server on port 3000 (via `yarn workspace @brokaid/web start`).
- Backend: FastAPI on port 8001 (via supervisor).
- MongoDB: local `mongodb://127.0.0.1:27017` , database `brokaid` (see `backend/.env`).

## Seeds

No real client data, ever. See `docs/SEEDS_ARE_FICTIONAL.md`. Canonical
demo bundle is composed by `backend/app/seeds/seed_all_mongo.py`
(Burmac Marine + Kelp Shipping fixtures, deterministic via seeded RNG).

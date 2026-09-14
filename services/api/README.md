# brokAId — services/api

FastAPI + PostgreSQL service for the brokAId agentic OS.

## Test suite

The default `pytest` invocation runs the deterministic tests only — no
live LLM calls, no external HTTP:

```
cd services/api
pytest
```

Live LLM-golden tests (intAIke / plAIce / complAInce / liAIson) are
gated behind an explicit env-var so demo-day CI never trips on LLM
non-determinism. To run them:

```
BROKAID_LLM_LIVE_TESTS=true pytest
```

`EMERGENT_LLM_KEY` must also be set for those tests to reach the
Emergent LLM proxy. Absent either, they skip cleanly.

## Seeding a fresh database

```
DATABASE_URL=postgresql://brokaid:brokaid_dev_local@127.0.0.1:5432/brokaid \
    python -c "import asyncio, asyncpg, os; \
               from app.seeds.seed_all import seed_all; \
               asyncio.run(seed_all(await asyncpg.create_pool(dsn=os.environ['DATABASE_URL'])))"
python -m app.seeds.ci_seed_cli
```

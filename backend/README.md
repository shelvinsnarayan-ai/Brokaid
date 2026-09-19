# brokAId — backend

FastAPI + PostgreSQL service for the brokAId agentic OS.

## Test suite

The default `pytest` invocation runs the deterministic tests only — no
live LLM calls, no external HTTP:

```
cd backend
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
               from app.seeds.seed_all import seed_all; \
python -m app.seeds.ci_seed_cli
```

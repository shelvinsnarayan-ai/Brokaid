# brokAId API

Phase 0: healthz + Postgres connection verification.

## Layout

```
services/api/
├── server.py         # supervisor shim — re-exports `app` from app.main
├── app/
│   ├── __init__.py
│   ├── main.py       # FastAPI app + lifespan
│   ├── db.py         # asyncpg connection pool
│   └── settings.py   # Pydantic settings
└── .env              # DATABASE_URL, CORS_ORIGINS, AUTH_DEV_MODE
```

## Run

Supervisor invokes uvicorn against `server:app` at `/app/backend`
(which is a symlink to this directory). The health check is at
`GET /api/healthz` and reports the connected Postgres version.

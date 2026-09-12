"""brokAId API — FastAPI application.

Phase 1 scope: trAIl (ledger) — migrations applied at startup,
`/api/trail/export`, `/api/o/matters`, `/api/healthz`.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

from app import db
from app.migrate import apply_migrations
from app.routes.matters import router as matters_router
from app.routes.trail import router as trail_router
from app.settings import get_settings

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brokaid.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # 1. apply migrations
    applied = await apply_migrations(settings.database_url)
    if applied:
        logger.info("migrations applied: %s", ", ".join(applied))
    else:
        logger.info("migrations: up to date")

    # 2. open the app connection pool
    pool = await db.init_pool(settings.database_url)
    app.state.pool = pool
    version = await db.fetch_version(pool)
    app.state.postgres_version = version
    logger.info("Connected to Postgres: %s", version.split(",")[0])
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(title="brokAId API", version="0.1.0", lifespan=lifespan)

api_router = APIRouter(prefix="/api")


@api_router.get("/healthz")
async def healthz() -> dict:
    pool = await db.get_pool()
    version = await db.fetch_version(pool)
    return {
        "status": "ok",
        "service": "brokaid-api",
        "postgres_version": version,
    }


app.include_router(api_router)
app.include_router(trail_router)
app.include_router(matters_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

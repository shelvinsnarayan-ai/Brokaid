"""brokAId API — FastAPI application.

Phase 0 scope: healthz + Postgres connection verification.
No tables, no auth, no business logic. See services/api/README.md.
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
from app.settings import get_settings

# Load .env from the services/api directory (which is where supervisor runs from
# via the /app/backend symlink).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brokaid.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = await db.init_pool(settings.database_url)
    version = await db.fetch_version(pool)
    app.state.postgres_version = version
    logger.info("Connected to Postgres: %s", version.split(",")[0])
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(
    title="brokAId API",
    version="0.0.1",
    lifespan=lifespan,
)

api_router = APIRouter(prefix="/api")


@api_router.get("/healthz")
async def healthz() -> dict:
    """Liveness + Postgres connectivity check.

    Returns the Postgres server version so the phase 0 done-when check
    can verify the API is actually talking to Postgres.
    """
    pool = await db.get_pool()
    version = await db.fetch_version(pool)
    return {
        "status": "ok",
        "service": "brokaid-api",
        "postgres_version": version,
    }


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

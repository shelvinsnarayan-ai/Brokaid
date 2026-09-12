"""brokAId API — FastAPI application factory.

Phase 4 adds:
  - auth routers (operator/client/dev)
  - compliance router
  - session middleware (attaches request.state.session lazily)
  - startup guard: AUTH_DEV_MODE=true + ENVIRONMENT=production → refuse.
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
from app.routes.auth_client import router as auth_client_router
from app.routes.auth_operator import router as auth_operator_router
from app.routes.compliance import router as compliance_router
from app.routes.matters import router as matters_router
from app.routes.trail import router as trail_router
from app.settings import get_settings

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brokaid.api")


def create_app(
    database_url: str | None = None,
    *,
    pool_override=None,
    seed_dev_users: bool | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Parameters
    ----------
    database_url
        DSN. Defaults to env `DATABASE_URL`.
    pool_override
        Pre-created asyncpg pool to reuse (tests inject the conftest pool).
        When set, lifespan skips its own migrations + pool bootstrap.
    seed_dev_users
        Force seeding on/off. Defaults to `AUTH_DEV_MODE`.
    """
    settings = get_settings()
    settings.assert_safe_to_start()
    dsn = database_url or settings.database_url
    do_seed = (
        seed_dev_users
        if seed_dev_users is not None
        else settings.auth_dev_mode
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if pool_override is not None:
            app.state.pool = pool_override
            app.state.postgres_version = "injected"
        else:
            applied = await apply_migrations(dsn)
            if applied:
                logger.info("migrations applied: %s", ", ".join(applied))
            pool = await db.init_pool(dsn)
            app.state.pool = pool
            version = await db.fetch_version(pool)
            app.state.postgres_version = version
            logger.info(
                "Connected to Postgres: %s", version.split(",")[0]
            )
        if do_seed:
            try:
                from app.seeds.seed_users import seed
                await seed(app.state.pool)
                logger.info("dev seeds applied")
            except Exception as e:
                logger.warning("dev seed failed (non-fatal): %r", e)
        try:
            yield
        finally:
            if pool_override is None:
                await db.close_pool()

    app = FastAPI(
        title="brokAId API", version="0.4.0", lifespan=lifespan,
    )

    api_router = APIRouter(prefix="/api")

    @api_router.get("/healthz")
    async def healthz() -> dict:
        pool = app.state.pool
        version = await db.fetch_version(pool)
        return {
            "status": "ok",
            "service": "brokaid-api",
            "postgres_version": version,
        }

    app.include_router(api_router)
    app.include_router(trail_router)
    app.include_router(matters_router)
    app.include_router(auth_operator_router)
    app.include_router(auth_client_router)
    app.include_router(compliance_router)
    # Phase 5 additions
    from app.routes.jurisdictions import router as jurisdictions_router
    from app.routes.policies import router as policies_router
    from app.routes.client_preferences import router as client_prefs_router
    app.include_router(jurisdictions_router)
    app.include_router(policies_router)
    app.include_router(client_prefs_router)

    # Dev-only impersonation route — mounted only under AUTH_DEV_MODE.
    if settings.auth_dev_mode:
        from app.routes.auth_dev import router as auth_dev_router
        app.include_router(auth_dev_router)

    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = create_app()

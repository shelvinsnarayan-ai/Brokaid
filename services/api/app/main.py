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
                from app.seeds.seed_all import seed_all
                await seed_all(app.state.pool)
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
    from app.routes.c_me import router as c_me_router
    app.include_router(jurisdictions_router)
    app.include_router(policies_router)
    app.include_router(client_prefs_router)
    app.include_router(c_me_router)
    # Phase 7 additions
    from app.routes.queue import router as queue_router
    from app.routes.kpi import router as kpi_router
    from app.routes.daily_brief import router as daily_brief_router
    from app.routes.client_360 import router as client_360_router
    app.include_router(queue_router)
    app.include_router(kpi_router)
    app.include_router(daily_brief_router)
    app.include_router(client_360_router)
    # Register the phase-7 compute_kpis job with the runner.
    from app.jobs import compute_kpis  # noqa: F401
    # Phase 9 additions — plAIce orchestration + pending quote surface.
    from app.routes.o_placement import router as o_placement_router
    from app.routes.c_pending_quote import router as c_pending_quote_router
    app.include_router(o_placement_router)
    app.include_router(c_pending_quote_router)
    # Phase 11 additions — reference scene + public verifier.
    from app.routes.c_scene import router as c_scene_router
    from app.routes.verifier import router as verifier_router
    from app.routes.c_cert import router as c_cert_router
    app.include_router(c_scene_router)
    app.include_router(verifier_router)
    app.include_router(c_cert_router)
    # Phase 12 addition — client Ask channel (liAIson).
    from app.routes.c_ask import router as c_ask_router
    app.include_router(c_ask_router)
    # Phase 13 additions — claim scene: client FNOL + operator status.
    from app.routes.c_claims import router as c_claims_router
    from app.routes.o_claims import router as o_claims_router
    app.include_router(c_claims_router)
    app.include_router(o_claims_router)
    # Phase 14 — Shadow Mode dashboard + export.
    from app.routes.o_shadow_mode import router as o_shadow_router
    app.include_router(o_shadow_router)

    # Dev-only impersonation + admin bundle download — mounted only
    # under AUTH_DEV_MODE. Settings.assert_safe_to_start already refuses
    # to boot with AUTH_DEV_MODE=true and ENVIRONMENT=production.
    if settings.auth_dev_mode:
        from app.routes.auth_dev import (
            admin_bundle_router,
            mount_admin_bundle_route,
            router as auth_dev_router,
        )
        app.include_router(auth_dev_router)
        bundle_path = os.environ.get("ADMIN_BUNDLE_PATH", "").strip()
        if bundle_path:
            try:
                ttl_min = int(os.environ.get("ADMIN_BUNDLE_TTL_MINUTES", "30"))
                mount_admin_bundle_route(
                    app, bundle_path=bundle_path, ttl_minutes=ttl_min
                )
                app.include_router(admin_bundle_router)
            except FileNotFoundError as e:
                logger.warning("admin bundle route not mounted: %s", e)
            except RuntimeError as e:
                logger.warning("admin bundle route refused: %s", e)

    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = create_app()

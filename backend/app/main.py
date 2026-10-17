"""brokAId API — FastAPI application factory (R5c-4b — Mongo-only).

Postgres runtime deleted. Mongo init is REQUIRED (fail-fast at
startup); `app.state.mongo_db` drives every route and `/api/healthz`.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.db import mongo as mongo_db
from app.middleware.tenant import TenantContextMiddleware
from app.settings import get_settings

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brokaid.api")


def create_app(
    *,
    mongo_db_override=None,
    seed_dev_users: bool | None = None,
) -> FastAPI:
    settings = get_settings()
    settings.assert_safe_to_start()
    do_seed = (
        seed_dev_users
        if seed_dev_users is not None
        else settings.auth_dev_mode
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if mongo_db_override is not None:
            app.state.mongo_db = mongo_db_override
            app.state.mongo_ready = True
            app.state.pool = None
            app.state.postgres_version = None
        else:
            await mongo_db.init_mongo_client()
            app.state.mongo_ready = True
            app.state.mongo_db = mongo_db.get_db_sync()
            app.state.pool = None
            app.state.postgres_version = None
            logger.info("Connected to Mongo (primary store)")

        if do_seed:
            try:
                from app.seeds.seed_all_mongo import seed_all_mongo
                await seed_all_mongo(app.state.mongo_db)
                logger.info("dev seeds applied (Mongo)")
            except Exception as e:  # noqa: BLE001
                logger.warning("dev seed failed (non-fatal): %r", e)

        if os.environ.get("BROKAID_SEED_CI", "").lower() == "true":
            try:
                from app.seeds.ci_deterministic import (
                    seed_ci_deterministic_mongo,
                )
                out = await seed_ci_deterministic_mongo(app.state.mongo_db)
                logger.info(
                    "CI deterministic seed applied: %s",
                    [t["slug"] for t in out.get("tenants", [])],
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("CI seed failed (non-fatal): %r", e)

        # R1 Blocker-1 close-out (2026-10-04): warn-level sanity check
        # that scans every policy and reports drift between
        # `schedule.limits.<slug>` and the authoritative
        # `schedule.cover_types[*].limit_any_one`. Non-fatal.
        try:
            from app.kernel.policy_sanity import (
                warn_on_policy_schedule_mismatches,
            )
            await warn_on_policy_schedule_mismatches(app.state.mongo_db)
        except Exception as e:  # noqa: BLE001
            logger.warning("policy sanity scan failed (non-fatal): %r", e)
        import asyncio
        from app.kernel import notify
        notify_task = asyncio.create_task(notify.loop(app.state.mongo_db))
        from app.kernel import email_channel
        email_task = asyncio.create_task(email_channel.loop(app.state.mongo_db))
        from app.kernel import push_channel
        push_task = asyncio.create_task(push_channel.loop(app.state.mongo_db))
        try:
            yield
        finally:
            notify_task.cancel()
            email_task.cancel()
            push_task.cancel()
            try:
                await mongo_db.close_mongo_client()
            except Exception as e:  # noqa: BLE001
                logger.warning("mongo close failed (non-fatal): %r", e)

    app = FastAPI(lifespan=lifespan, openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TenantContextMiddleware)

    api_router = APIRouter()

    @api_router.get("/api/healthz")
    async def healthz() -> dict:
        mongo_status = await mongo_db.ping_mongo(app.state.mongo_db)
        return {
            "status": "ok",
            "service": "brokaid-api",
            "mongo": mongo_status,
            "postgres": None,
        }

    from app.routes.auth_client import router as auth_client_router
    from app.routes.build_info import router as build_info_router
    from app.routes.c_envelope import router as c_envelope_router
    from app.routes.auth_operator import router as auth_operator_router
    from app.routes.c_ask import router as c_ask_router
    from app.routes.c_cert import router as c_cert_router
    from app.routes.c_claims import router as c_claims_router
    from app.routes.c_me import router as c_me_router
    from app.routes.c_next_action import router as c_next_action_router
    from app.routes.c_pending_quote import router as c_pending_quote_router
    from app.routes.c_scene import router as c_scene_router
    from app.routes.client_360 import router as client_360_router
    from app.routes.client_preferences import router as client_preferences_router
    from app.routes.compliance import router as compliance_router
    from app.routes.daily_brief import router as daily_brief_router
    from app.routes.jurisdictions import router as jurisdictions_router
    from app.routes.kpi import router as kpi_router
    from app.routes.matters import router as matters_router
    from app.routes.o_change_packet import router as o_change_packet_router
    from app.routes.o_claims import router as o_claims_router
    from app.routes.o_governance import router as o_governance_router
    from app.routes.o_branding import router as o_branding_router
    from app.routes.o_release_check import router as o_release_check_router
    from app.routes.o_m2_stubs import router as o_m2_stubs_router
    from app.routes.notifications import router as notifications_router
    from app.routes.c_things import router as c_things_router
    from app.routes.o_placement import router as o_placement_router
    from app.routes.o_shadow_mode import router as o_shadow_mode_router
    from app.routes.policies import router as policies_router
    from app.routes.queue import router as queue_router
    from app.routes.trail import router as trail_router
    from app.routes.verifier import router as verifier_router

    for r in (
        build_info_router,
        c_envelope_router,
        auth_client_router, auth_operator_router,
        c_ask_router, c_cert_router, c_claims_router, c_me_router,
        c_next_action_router, c_pending_quote_router, c_scene_router,
        client_360_router, client_preferences_router,
        compliance_router, daily_brief_router, jurisdictions_router,
        kpi_router, matters_router,
        o_change_packet_router, o_claims_router, o_governance_router, o_branding_router, o_release_check_router, o_m2_stubs_router, notifications_router, c_things_router, o_placement_router, o_shadow_mode_router,
        policies_router, queue_router, trail_router, verifier_router,
    ):
        api_router.include_router(r)

    if settings.auth_dev_mode:
        from app.routes.auth_dev import router as auth_dev_router
        api_router.include_router(auth_dev_router)
    app.include_router(api_router)
    return app


app = create_app()

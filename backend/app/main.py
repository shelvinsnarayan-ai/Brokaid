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

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # before app imports: COOKIE_NAME reads APP_BASE_URL at import

from fastapi import APIRouter, FastAPI  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

from app.db import mongo as mongo_db  # noqa: E402
from app.middleware.tenant import TenantContextMiddleware  # noqa: E402
from app.settings import get_settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brokaid.api")
# Hardening: uvicorn's access log prints raw paths + query strings (invite/magic-link tokens). Our JSON access log
# (route template, hashed tenant, no bodies) replaces it.
logging.getLogger("uvicorn.access").disabled = True


CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_HEADERS = ["Content-Type", "X-Requested-With", "X-Tenant-Slug", "X-Request-ID"]  # every custom header the frontend sends


def create_app(
    *,
    mongo_db_override=None,
    seed_dev_users: bool | None = None,
) -> FastAPI:
    from app.kernel import devmode as _devmode_mod
    settings = get_settings()
    settings.assert_safe_to_start()
    do_seed = (
        seed_dev_users
        if seed_dev_users is not None
        else settings.auth_dev_mode  # devmode.dev_mode(): explicit dev ENVIRONMENT + AUTH_DEV_MODE
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

        from app.kernel import devmode as _devmode
        await _devmode.boot_guard(app.state.mongo_db)  # SEC-001c: dev mode never runs beside real tenant data
        if not _devmode.dev_mode():
            try:
                from app.kernel import demo_credentials
                await demo_credentials.disable_all(app.state.mongo_db)  # R3.10: demo passwords never work outside dev mode
            except Exception as e:  # noqa: BLE001
                logger.critical("demo credential disable failed: %r", e)
        if do_seed and _devmode.dev_mode():
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
        import os as _os
        from app.kernel import auth_flows, jobs, notify
        try:
            logger.info("identities migrated: %s", await auth_flows.migrate_identities(app.state.mongo_db))
            logger.info("bootstrap operator: %s", await auth_flows.bootstrap_operator(app.state.mongo_db))
            from app.kernel import platform as _platform
            logger.info("bootstrap platform admin: %s", await _platform.bootstrap(app.state.mongo_db))
        except Exception as e:  # noqa: BLE001
            logger.warning("bootstrap operator failed (non-fatal): %r", e)
        await notify.ensure_indexes(app.state.mongo_db)
        from app.kernel import acceptance as _acceptance
        await _acceptance.ensure_indexes(app.state.mongo_db)
        from app.kernel import compliance_library as _library
        await _library.ensure_indexes(app.state.mongo_db)
        # R1.3: identity uniqueness indexes at boot, not lazily on first domain write (fresh DBs had none).
        from app.db.tenant_safe import wrap as _wrap
        from app.kernel.identity_mongo import _ensure_indexes as _identity_indexes
        await _identity_indexes(_wrap(app.state.mongo_db))
        from app.kernel import bankfeed as _bankfeed
        await _bankfeed.ensure_indexes(app.state.mongo_db)
        from app.kernel import renewals as _renewals
        await _renewals.ensure_indexes(app.state.mongo_db)
        from app.kernel import compliance_docs as _cdocs
        await _cdocs.ensure_indexes(app.state.mongo_db)
        from app.kernel import ops as _ops
        await _ops.ensure_indexes(app.state.mongo_db)
        await app.state.mongo_db.push_subscriptions.create_index(
            [("tenant_id", 1), ("user_id", 1), ("endpoint", 1)], unique=True)
        # R1.1: background work runs as durable jobs (notify fan-out, intake chain, email, push, periodic checks).
        job_tasks = await jobs.start_in_process(app.state.mongo_db, workers=int(_os.environ.get("JOBS_WORKERS", "1")))
        import asyncio as _asyncio
        job_tasks.append(_asyncio.create_task(_ops.watch_loop(app.state.mongo_db)))  # R1.9 owner alerts (leader-independent)
        try:
            yield
        finally:
            for t in job_tasks:
                t.cancel()
            try:
                await mongo_db.close_mongo_client()
            except Exception as e:  # noqa: BLE001
                logger.warning("mongo close failed (non-fatal): %r", e)

    app = FastAPI(lifespan=lifespan, openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
    )
    app.add_middleware(TenantContextMiddleware)
    from app.kernel.ops import AccessLogMiddleware
    from app.middleware.security import SecurityMiddleware
    app.add_middleware(SecurityMiddleware)   # R1.9: headers, CSRF origin check, rate limits
    app.add_middleware(AccessLogMiddleware)  # R1.9: outermost — JSON access log + error capture

    from fastapi import Depends
    from app.middleware.client_guard import client_object_guard
    api_router = APIRouter(dependencies=[Depends(client_object_guard)])  # R1.2b: foreign client objects → 404

    @api_router.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @api_router.get("/api/ready")
    async def ready_check():
        from fastapi.responses import JSONResponse
        from app.kernel import ops as _ops
        r = await _ops.ready(app.state.mongo_db)
        return JSONResponse(r, status_code=200 if r["ok"] else 503)

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
    from app.routes.auth_login import router as auth_login_router
    from app.routes.o_invites import router as o_invites_router
    from app.routes.o_users import router as o_users_router
    from app.routes.o_import import router as o_import_router
    from app.routes.o_bank import c_router as c_money_router
    from app.routes.o_bank import router as o_bank_router
    from app.routes.o_renewals import c_router as c_renewals_router
    from app.routes.o_renewals import router as o_renewals_router
    from app.routes.o_renewal_review import router as o_renewal_review_router
    from app.routes.o_compliance import c_router as c_compliance_router
    from app.routes.o_compliance import router as o_compliance_router
    from app.routes.c_members import router as c_members_router
    from app.routes.c_people import router as c_people_router
    from app.routes.platform import router as platform_router
    from app.routes.c_ask import router as c_ask_router
    from app.routes.c_cert import router as c_cert_router
    from app.routes.c_claims import router as c_claims_router
    from app.routes.c_me import router as c_me_router
    from app.routes.c_next_action import router as c_next_action_router
    from app.routes.c_home import router as c_home_router
    from app.routes.c_claim_threads import router as c_claim_threads_router
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
    from app.routes.acceptance import c_router as acceptance_c_router, o_router as acceptance_o_router
    from app.routes.o_governance import router as o_governance_router
    from app.routes.o_branding import router as o_branding_router
    from app.routes.o_release_check import router as o_release_check_router
    from app.routes.o_jobs import router as o_jobs_router
    from app.routes.o_m2_stubs import router as o_m2_stubs_router
    from app.routes.notifications import router as notifications_router
    from app.routes.me_push import router as me_push_router
    from app.routes.o_reviews import router as o_reviews_router
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
        auth_login_router, o_invites_router, o_users_router, o_import_router, o_bank_router, c_money_router, o_renewals_router, o_renewal_review_router, c_renewals_router, o_compliance_router, c_compliance_router, c_members_router, c_people_router, platform_router,
        c_ask_router, c_cert_router, c_claims_router, c_me_router,
        c_next_action_router, c_home_router, c_claim_threads_router, c_pending_quote_router, c_scene_router,
        client_360_router, client_preferences_router,
        compliance_router, daily_brief_router, jurisdictions_router,
        kpi_router, matters_router,
        o_change_packet_router, o_claims_router, acceptance_c_router, acceptance_o_router, o_governance_router, o_branding_router, o_release_check_router, o_jobs_router, o_m2_stubs_router, notifications_router, me_push_router, o_reviews_router, c_things_router, o_placement_router, o_shadow_mode_router,
        policies_router, queue_router, trail_router, verifier_router,
    ):
        api_router.include_router(r)

    # R1.2a: legacy sign-in routes (magic link, operator-only login) never ship to production.
    # R3.10: mounted only in dev mode (dev ENVIRONMENT + AUTH_DEV_MODE + preview APP_BASE_URL); every request is also host-checked.
    from fastapi import Depends as _Depends
    _preview_only = [_Depends(_devmode_mod.require_preview_request)]
    if settings.auth_dev_mode:  # SEC-001a: explicit dev ENVIRONMENT + AUTH_DEV_MODE only
        api_router.include_router(auth_client_router, dependencies=_preview_only)
        api_router.include_router(auth_operator_router, dependencies=_preview_only)

    if settings.auth_dev_mode:
        from app.routes.auth_dev import router as auth_dev_router
        api_router.include_router(auth_dev_router, dependencies=_preview_only)
    app.include_router(api_router)
    return app


app = create_app()

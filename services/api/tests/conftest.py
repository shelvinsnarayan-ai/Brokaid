"""Shared pytest fixtures for the brokAId API.

Uses a scratch database `brokaid_test` on the local dev Postgres.
Fresh migrations per session; each test isolates via TRUNCATE.

Phase 18 addition — `pytest_collection_modifyitems` transforms every
test carrying the `requires_llm_key` marker into a conditional skip
when `BROKAID_LLM_LIVE_TESTS != "true"`. This keeps the default
`pytest` invocation free of live-LLM non-determinism (a hard
requirement for demo-day CI) without silently xfailing or marking
individual tests flaky. Run `BROKAID_LLM_LIVE_TESTS=true pytest` to
opt in.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def pytest_collection_modifyitems(config, items):
    """Skip every LLM-live golden test unless BROKAID_LLM_LIVE_TESTS=true.

    Applies on top of the existing `requires_llm_key` marker; the pre-
    Phase-18 EMERGENT_LLM_KEY skipif inside each test file still fires
    when the key is missing.
    """
    if os.environ.get("BROKAID_LLM_LIVE_TESTS", "").lower() == "true":
        return
    skip = pytest.mark.skip(
        reason="LLM-live goldens gated by BROKAID_LLM_LIVE_TESTS=true "
               "(phase 18 — LLM non-determinism keeps demo CI green)."
    )
    for item in items:
        if "requires_llm_key" in item.keywords:
            item.add_marker(skip)

TEST_DB = "brokaid_test"
ADMIN_DSN_TEMPLATE = "postgresql://{user}:{password}@{host}:{port}/postgres"
TEST_DSN_TEMPLATE = "postgresql://{user}:{password}@{host}:{port}/{db}"


def _parse_dsn(dsn: str) -> dict:
    from urllib.parse import urlparse
    u = urlparse(dsn)
    return {
        "user": u.username, "password": u.password,
        "host": u.hostname, "port": u.port or 5432,
    }


def _test_dsn() -> str:
    prod = os.environ["DATABASE_URL"]
    p = _parse_dsn(prod)
    return TEST_DSN_TEMPLATE.format(**p, db=TEST_DB)


def _admin_dsn() -> str:
    prod = os.environ["DATABASE_URL"]
    p = _parse_dsn(prod)
    return ADMIN_DSN_TEMPLATE.format(**p)


@pytest_asyncio.fixture(scope="session")
async def _test_db_url() -> str:
    admin = await asyncpg.connect(_admin_dsn())
    try:
        # Drop and recreate to guarantee a clean slate.
        await admin.execute(
            f'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
            f"WHERE datname='{TEST_DB}' AND pid <> pg_backend_pid()"
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await admin.close()

    from app.migrate import apply_migrations
    dsn = _test_dsn()
    await apply_migrations(dsn)
    yield dsn


@pytest_asyncio.fixture
async def pool(_test_db_url) -> asyncpg.Pool:
    # Phase 17 hotfix: force a single physical connection for the test
    # pool. The `seed_users.seed()` helper acquires the pool three
    # separate times (`_ensure_tenant`, `_ensure_brokerage`,
    # `_ensure_operator`), and under the full-suite run order a
    # committed brokerage row occasionally failed to surface to the
    # subsequent `_ensure_operator` acquire, producing a spurious
    # user_brokerage_roles → brokerages FK violation. Serialising on
    # one connection is a strict test-fixture-only change; production
    # boot uses whatever pool sizing app/db.init_pool() picks.
    pool = await asyncpg.create_pool(dsn=_test_db_url, min_size=1, max_size=1)
    # Wipe events + projections + tenants before each test.
    async with pool.acquire() as c:
        # DISCARD ALL clears any lingering prepared-statement cache,
        # session-level `SET`s, and advisory locks left by a previous
        # test. Cheap and idempotent.
        await c.execute("DISCARD ALL")
        await c.execute("ALTER TABLE proj_matters NO FORCE ROW LEVEL SECURITY")
        await c.execute("ALTER TABLE events NO FORCE ROW LEVEL SECURITY")
        # Phase 4 adds users/brokerages/roles/sessions/magic_link_tokens/
        # user_client_links. `users` has no FK to tenants so must be
        # named explicitly. Everything else cascades via tenants.
        # Phase 5 adds clients/policies. TRUNCATE cascades via tenants FK.
        await c.execute(
            "TRUNCATE TABLE proj_notifications, proj_claim_status_updates, "
            "claims, "
            "verifier_tokens, proj_queue, proj_kpi_daily, "
            "proj_sprint_usage_by_agent, "
            "policies, clients, "
            "sessions, magic_link_tokens, user_client_links, "
            "user_brokerage_roles, brokerages, users, "
            "proj_matters, events, tenants "
            "RESTART IDENTITY CASCADE"
        )
        # Post-condition: TRUNCATE actually zeroed the tables the seed
        # touches. If this ever fires it's a schema drift bug.
        n_brokerages = await c.fetchval("SELECT COUNT(*) FROM brokerages")
        n_tenants = await c.fetchval("SELECT COUNT(*) FROM tenants")
        assert n_brokerages == 0 and n_tenants == 0, (
            f"TRUNCATE left rows: brokerages={n_brokerages} tenants={n_tenants}"
        )
        await c.execute("ALTER TABLE events FORCE ROW LEVEL SECURITY")
        await c.execute("ALTER TABLE proj_matters FORCE ROW LEVEL SECURITY")
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---- Phase 4 --------------------------------------------------------------


@pytest_asyncio.fixture
async def app_client(pool, monkeypatch):
    """FastAPI app bound to the test pool + an httpx.AsyncClient."""
    import httpx

    # Force AUTH_DEV_MODE=true so the dev impersonation route mounts.
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Ensure no accidental RESEND path.
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    from app.main import create_app
    app = create_app(pool_override=pool, seed_dev_users=False)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield app, client


@pytest_asyncio.fixture
async def seeded(pool):
    """Seed users + brokerage + client links; return the seed result dict."""
    from app.seeds.seed_users import seed
    return await seed(pool)



@pytest_asyncio.fixture
async def scene_seeded(pool):
    """Full seed for the phase-11 reference scene: users + org + clients +
    policies + historical + vulnerable client. Returns the seed_all shape
    plus a convenience `principal_id`.
    """
    from app.seeds.seed_all import seed_all
    result = await seed_all(pool)
    for op in result.get("operators", []):
        if op.get("role") == "principal":
            result["principal_id"] = op["user_id"]
            break
    for cl in result.get("clients", []):
        if cl.get("role") in ("client_owner", "owner") or "owner" in cl.get("role", ""):
            result["client_user_id"] = cl["user_id"]
            break
    return result

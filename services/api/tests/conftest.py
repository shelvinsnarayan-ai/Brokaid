"""Shared pytest fixtures for the brokAId API.

Uses a scratch database `brokaid_test` on the local dev Postgres.
Fresh migrations per session; each test isolates via TRUNCATE.
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
    pool = await asyncpg.create_pool(dsn=_test_db_url, min_size=1, max_size=5)
    # Wipe events + proj + tenants before each test.
    async with pool.acquire() as c:
        # Need to bypass RLS: TRUNCATE requires the row set to match policy,
        # but the table owner with FORCE RLS also can't bypass — so we
        # temporarily disable FORCE, truncate, and re-enable.
        await c.execute("ALTER TABLE proj_matters NO FORCE ROW LEVEL SECURITY")
        await c.execute("ALTER TABLE events NO FORCE ROW LEVEL SECURITY")
        await c.execute("TRUNCATE TABLE proj_matters, events, tenants RESTART IDENTITY CASCADE")
        await c.execute("ALTER TABLE events FORCE ROW LEVEL SECURITY")
        await c.execute("ALTER TABLE proj_matters FORCE ROW LEVEL SECURITY")
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

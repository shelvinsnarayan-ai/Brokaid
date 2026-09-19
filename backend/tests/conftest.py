"""Shared pytest fixtures for the brokAId API (R5c-4b — Mongo-only)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class _PgDeleted:
    """Sentinel installed as `app.state.pool` under R5c-4a/b so any
    legacy test that reads `pool = app.state.pool; await pool.acquire()`
    fails cleanly as a pytest.skip rather than an AttributeError."""

    _MSG = (
        "PG-only test path: `app.state.pool` accessed after Postgres "
        "was deleted in R5c-4a. Test needs Mongo migration or should "
        "be deleted."
    )

    def __getattr__(self, name):
        pytest.skip(self._MSG)

    def __getitem__(self, key):
        pytest.skip(self._MSG)

    def __bool__(self) -> bool:
        pytest.skip(self._MSG)

    def acquire(self):
        pytest.skip(self._MSG)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("BROKAID_LLM_LIVE_TESTS", "").lower() == "true":
        return
    skip = pytest.mark.skip(
        reason="LLM-live goldens gated by BROKAID_LLM_LIVE_TESTS=true",
    )
    for item in items:
        if "requires_llm_key" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def pool():
    """R5c-4a: Postgres runtime deleted. Any test still requesting
    this fixture is a pure-PG-schema test whose Mongo equivalents live
    in the `*_mongo_reference.py` / `test_r5*_mongo.py` suites."""
    pytest.skip(
        "R5c-4a/b: Postgres deleted; test uses raw pool.acquire() SQL."
    )


@pytest_asyncio.fixture(scope="function")
async def mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    raw = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
    url = raw.replace("://localhost:", "://127.0.0.1:") if "localhost" in raw else raw
    dbname = f"brokaid_test_{os.getpid()}"
    client = AsyncIOMotorClient(url, uuidRepresentation="standard")
    await client.drop_database(dbname)
    try:
        yield client[dbname]
    finally:
        await client.drop_database(dbname)
        client.close()


@pytest_asyncio.fixture(scope="function")
async def app_client(mongo_db):
    """Mongo-only ASGI transport client. Yields `(app, client)` tuple
    for legacy callers that unpack it. `app.state.pool` is set to a
    `_PgDeleted` sentinel so legacy PG-only tests skip cleanly."""
    import httpx
    from app.main import create_app
    app = create_app(mongo_db_override=mongo_db, seed_dev_users=False)
    async with app.router.lifespan_context(app):
        # Post-lifespan: install `_PgDeleted` sentinel so legacy tests that
        # `pool = app.state.pool; await pool.acquire()` skip cleanly rather
        # than crash on `None.acquire()`.
        app.state.pool = _PgDeleted()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            yield app, client


@pytest_asyncio.fixture(scope="function")
async def seeded(mongo_db):
    """Run the canonical Mongo demo bundle (Burmac Marine + Kelp
    Shipping fixtures) against a fresh test DB. Returns the seed
    result dict."""
    from app.seeds.seed_all_mongo import seed_all_mongo
    return await seed_all_mongo(mongo_db)


@pytest_asyncio.fixture(scope="function")
async def scene_seeded(mongo_db):
    """Alias of `seeded` for reference-scene tests (R5b+)."""
    from app.seeds.seed_all_mongo import seed_all_mongo
    return await seed_all_mongo(mongo_db)

"""Shared pytest fixtures for the brokAId API (R5c-4b — Mongo-only)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# ---- R1.3: every run / xdist worker gets throwaway databases + its own API server ------------
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

TEST_PREFIX = "brokaid_test_"
_RUN = os.environ.get("BROKAID_TEST_RUN") or uuid.uuid4().hex[:8]
os.environ["BROKAID_TEST_RUN"] = _RUN
_LIVE_DB = os.environ.get("DB_NAME", "")
_SERVER: subprocess.Popen | None = None


def _worker() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "gw0")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _guard_db(name: str) -> None:
    if not name.startswith(TEST_PREFIX) or name == _LIVE_DB:
        raise pytest.UsageError(f"refusing to run tests against non-test database {name!r}")


_DB = None


def pytest_configure(config):
    global _SERVER, _DB
    db = _DB = f"{TEST_PREFIX}{_RUN}_{_worker()}"
    _guard_db(db)
    os.environ["DB_NAME"] = db
    os.environ.setdefault("ENVIRONMENT", "development")
    is_xdist_controller = bool(getattr(config.option, "numprocesses", None)) and not os.environ.get("PYTEST_XDIST_WORKER")
    if os.environ.get("BROKAID_TEST_NO_SERVER") == "1" or is_xdist_controller:
        return
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "DB_NAME": db, "AUTH_DEV_MODE": "true", "ENVIRONMENT": "development",
           "APP_BASE_URL": base, "EMAIL_ENABLED": "false", "EMAIL_AUTH_ENABLED": "false", "PUSH_ENABLED": "false"}
    _guard_db(env["DB_NAME"])
    log = open(f"/tmp/brokaid_test_api_{_RUN}_{_worker()}.log", "w")
    _SERVER = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                               cwd=str(Path(__file__).resolve().parents[1]), env=env, stdout=log, stderr=log)
    import httpx
    for _ in range(240):
        try:
            if httpx.get(f"{base}/api/healthz", timeout=1).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        raise pytest.UsageError(f"test API server didn't start; see {log.name}")
    os.environ["REACT_APP_BACKEND_URL"] = base  # live-API tests talk to this throwaway server, never preview


def pytest_unconfigure(config):
    if _SERVER:
        _SERVER.terminate()
        try:
            _SERVER.wait(timeout=10)
        except Exception:
            _SERVER.kill()
    import pymongo
    c = pymongo.MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    # workers drop their own DBs (incl. suffixed / nested ones); the xdist controller or a plain run sweeps the whole run
    prefix = _DB if os.environ.get("PYTEST_XDIST_WORKER") else f"{TEST_PREFIX}{_RUN}_"
    for name in c.list_database_names():
        if name.startswith(prefix):
            c.drop_database(name)
    c.close()


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
    dbname = f"{os.environ['DB_NAME']}_{uuid.uuid4().hex[:6]}"
    _guard_db(dbname)
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

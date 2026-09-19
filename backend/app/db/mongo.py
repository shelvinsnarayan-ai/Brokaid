"""Phase R1 MongoDB (Motor) client scaffolding.

Emergent-native deployment expects a Mongo store; the rewrite arc
(R1–R6) moves brokAId's ledger + read models off PostgreSQL onto
MongoDB. R1 lands the client factory + a `get_db()` FastAPI
dependency but does NOT migrate any collection yet — (mongo) + the
ledger schema remain authoritative through R4.

Ground-rule reminder: the append-only guarantee, tenant isolation
guarantee, and hash-chain semantics MUST survive the move. In R4+
those switch from database-enforced (RLS + INSERT-only triggers) to
application-enforced. Do NOT special-case those semantics from
inside this module — this is transport, not policy.

Usage:
    from app.db.mongo import get_db, get_mongo_client, ping_mongo

    @router.get("/healthz")
    async def healthz(db = Depends(get_db)):
        return {"mongo": await ping_mongo(db)}
"""
from __future__ import annotations

import logging
import os
from typing import AsyncIterator

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger("brokaid.mongo")

_client: AsyncIOMotorClient | None = None
_db_name: str | None = None


def _mongo_url() -> str:
    """Read MONGO_URL from the environment at call time so tests can
    override it after import."""
    url = os.environ.get("MONGO_URL")
    if not url:
        raise RuntimeError(
            "MONGO_URL is not set. Populate backend/.env or export it "
            "before starting the API."
        )
    return url


def _db_name_env() -> str:
    """Read DB_NAME from the environment at call time. Same reasoning."""
    name = os.environ.get("DB_NAME")
    if not name:
        raise RuntimeError(
            "DB_NAME is not set. Populate backend/.env or export it "
            "before starting the API."
        )
    return name


async def init_mongo_client() -> AsyncIOMotorClient:
    """Create the process-wide Motor client with pooled connections.

    Called from the FastAPI lifespan startup hook. Safe to call more
    than once — subsequent calls return the existing client so tests
    can pin a fresh event loop without leaking pools.
    """
    global _client, _db_name
    if _client is not None:
        return _client
    url = _mongo_url()
    _db_name = _db_name_env()
    _client = AsyncIOMotorClient(
        url,
        # Motor 3.x pools default to maxPoolSize=100 which is fine for
        # Emergent's single-pod deployment; pin the settings we care
        # about explicitly so upstream default drift can't bite us.
        maxPoolSize=50,
        minPoolSize=1,
        serverSelectionTimeoutMS=5_000,
        uuidRepresentation="standard",
    )
    logger.info("mongo client initialised (db=%s)", _db_name)
    return _client


async def close_mongo_client() -> None:
    """Called from FastAPI lifespan shutdown."""
    global _client, _db_name
    if _client is not None:
        _client.close()
        _client = None
        _db_name = None
        logger.info("mongo client closed")


def get_mongo_client() -> AsyncIOMotorClient:
    """Return the initialised client. Raises if `init_mongo_client()`
    hasn't been called yet (e.g. tests hitting a route without the
    lifespan running)."""
    if _client is None:
        raise RuntimeError(
            "mongo client not initialised — call init_mongo_client() "
            "from the FastAPI lifespan startup hook first."
        )
    return _client


def get_db_sync() -> AsyncIOMotorDatabase | None:
    """Return the initialised database handle synchronously, or None if
    the client hasn't been initialised yet. Used by
    `TenantContextMiddleware` (R4a) to fetch the store without needing
    an `async with` — the middleware is already inside a coroutine
    but the Motor client + db handle are synchronous accessors.
    """
    if _client is None or _db_name is None:
        return None
    return _client[_db_name]


async def get_db() -> AsyncIterator[AsyncIOMotorDatabase]:
    """FastAPI dependency yielding the tenant-scoped Mongo DB handle.

    R1: no per-request state; simply hands out the shared handle.
    R2+ will attach tenant filters via `request.state.session`.
    """
    client = get_mongo_client()
    assert _db_name is not None  # noqa: S101 — enforced by init
    yield client[_db_name]


async def ping_mongo(db: AsyncIOMotorDatabase | None = None) -> dict[str, object]:
    """Health probe used by `/api/healthz`. Returns a dict shaped like
    `{"ok": true, "server_version": "7.0.5"}` on success or
    `{"ok": false, "error": "..."}` on failure so the healthz surface
    can carry the failure cause without raising."""
    try:
        if db is None:
            client = get_mongo_client()
            assert _db_name is not None
            db = client[_db_name]
        # `ping` is cheap and works without any authentication scope.
        result = await db.command("ping")
        server_info = await db.client.server_info()
        return {
            "ok": bool(result.get("ok")),
            "server_version": server_info.get("version"),
        }
    except Exception as e:  # noqa: BLE001 — surface the raw reason
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

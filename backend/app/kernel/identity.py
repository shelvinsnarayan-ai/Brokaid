"""brokAId kernel — identity dispatcher (R4a).

Consolidates the identity + auth surface into a single kernel module
gated by `BROKAID_IDENTITY=postgres|mongo` (default mongo as of
R5c-2b — the R5c flip made Mongo the primary store). R4a
adds the dispatcher + Mongo backend WITHOUT rewiring existing
callers — routes/agents continue to hit `auth.sessions.load(pool)`
etc. directly. Only new callers (middleware, parity tests) use the
dispatcher today. R4b+ will migrate the rest.

Public API (byte-identical shape across backends)
-------------------------------------------------
Session lifecycle:
    read_session(store, session_id) -> Optional[Session]
    create_session(store, *, user_id, tenant_id, active_role,
                   brokerage_id=None, client_id=None,
                   mfa_verified=False, ttl=SESSION_TTL) -> Session
    revoke_session(store, session_id) -> None
    mark_mfa_verified(store, session_id) -> None
    update_active_context(store, session_id, *, tenant_id,
                          client_id, active_role) -> None

Users (tenant-scoped once ctx set):
    find_user_by_email(store, email) -> Optional[User]
    find_user_by_id(store, user_id) -> Optional[User]

Tenants (system-only — no ctx needed):
    find_tenant_by_slug(store, slug) -> Optional[Tenant]
    find_tenant_by_id(store, tenant_id) -> Optional[Tenant]
    list_all_tenants(store) -> list[Tenant]

Roles:
    list_brokerage_roles_for_user(store, user_id) -> list[BrokerageRole]
    list_client_links_for_user(store, user_id) -> list[ClientLink]

Backend selection
-----------------
`store` is intentionally polymorphic: `object` for Postgres,
`AsyncIOMotorDatabase` (or `TenantSafeDatabase`) for Mongo. Every
function inspects the store type first; if the store type
contradicts the configured `BROKAID_IDENTITY` flag, raises
`IdentityBackendMismatch`. This lets tests exercise both backends
by constructing the right store, while production callers pick
one path via the flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import sessions as _pg_sessions
from app.auth.sessions import COOKIE_NAME, SESSION_TTL, Session
from app.db.tenant_safe import TenantSafeDatabase


class IdentityBackendMismatch(RuntimeError):
    """`store` type doesn't match the configured backend."""


# ---- Dataclasses shared across backends -------------------------------
@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    display_name: str
    kind: str                 # "operator" | "client"
    password_hash: Optional[str]
    mfa_secret: Optional[str]


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    slug: str
    display_name: str
    genesis_hash: str


@dataclass(frozen=True)
class BrokerageRole:
    user_id: str
    tenant_id: str
    brokerage_id: str
    role: str
    active: bool


@dataclass(frozen=True)
class ClientLink:
    user_id: str
    tenant_id: str
    client_id: str
    role: str


def active_backend() -> str:
    """Return `postgres` or `mongo`. Read at call-time so tests can
    flip via monkeypatch. Default `postgres` — R4a keeps existing
    routes on Postgres; only the parity test flips to Mongo."""
    # R5c-4b — Mongo-only. Kept for API compatibility with legacy callers.
    return "mongo"


def _is_mongo_store(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


def _is_pg_store(store) -> bool:
    # R5c-4b — Postgres runtime deleted. Every store passed in is Mongo.
    return False


def _dispatch(store):
    """Import the right backend module for this store. Isolated import
    so the Mongo path never touches (mongo) at module-load time (test
    envs without pg still work)."""
    if _is_mongo_store(store):
        from app.kernel import identity_mongo
        return identity_mongo
    if _is_pg_store(store):
        return _PgBackend()
    raise IdentityBackendMismatch(
        f"unknown store type: {type(store).__name__}. Expected "
        f"object or AsyncIOMotorDatabase / TenantSafeDatabase."
    )


# ---- Postgres backend (thin adapter over existing modules) ------------
class _PgBackend:
    """Postgres-side identity impl. Delegates session ops to the
    existing `app.auth.sessions` module (byte-identical to R3); adds
    users/tenants/roles helpers that were previously inline SQL in
    routes/seeds. This is a pure adapter — no schema change."""

    async def read_session(self, pool, session_id):
        return await _pg_sessions.load(pool, session_id)

    async def create_session(self, pool, *, user_id, tenant_id,
                              active_role, brokerage_id=None,
                              client_id=None, mfa_verified=False,
                              ttl=SESSION_TTL):
        return await _pg_sessions.create(
            pool, user_id=user_id, tenant_id=tenant_id,
            active_role=active_role, brokerage_id=brokerage_id,
            client_id=client_id, mfa_verified=mfa_verified, ttl=ttl,
        )

    async def revoke_session(self, pool, session_id):
        await _pg_sessions.revoke(pool, session_id)

    async def mark_mfa_verified(self, pool, session_id):
        await _pg_sessions.mark_mfa_verified(pool, session_id)

    async def update_active_context(self, pool, session_id, *,
                                     tenant_id, client_id, active_role):
        await _pg_sessions.update_active_context(
            pool, session_id, tenant_id=tenant_id,
            client_id=client_id, active_role=active_role,
        )

    async def find_user_by_email(self, pool, email):
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT user_id::text AS user_id, email, display_name, "
                "       kind, password_hash, mfa_secret "
                "FROM users WHERE email = $1", email,
            )
        return _user_from_row(row) if row else None

    async def find_user_by_id(self, pool, user_id):
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT user_id::text AS user_id, email, display_name, "
                "       kind, password_hash, mfa_secret "
                "FROM users WHERE user_id = $1::uuid", user_id,
            )
        return _user_from_row(row) if row else None

    async def set_mfa_secret(self, pool, user_id, secret):
        async with pool.acquire() as c:
            await c.execute(
                "UPDATE users SET mfa_secret = $1 WHERE user_id = $2::uuid",
                secret, user_id,
            )

    async def find_tenant_by_slug(self, pool, slug):
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT tenant_id::text AS tenant_id, slug, display_name, "
                "       genesis_hash FROM tenants WHERE slug = $1", slug,
            )
        return _tenant_from_row(row) if row else None

    async def find_tenant_by_id(self, pool, tenant_id):
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT tenant_id::text AS tenant_id, slug, display_name, "
                "       genesis_hash FROM tenants WHERE tenant_id = $1::uuid",
                tenant_id,
            )
        return _tenant_from_row(row) if row else None

    async def list_all_tenants(self, pool):
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT tenant_id::text AS tenant_id, slug, display_name, "
                "       genesis_hash FROM tenants ORDER BY slug",
            )
        return [_tenant_from_row(r) for r in rows]

    async def list_brokerage_roles_for_user(self, pool, user_id):
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT ubr.user_id::text AS user_id, "
                "       b.tenant_id::text AS tenant_id, "
                "       ubr.brokerage_id::text AS brokerage_id, "
                "       ubr.role, ubr.active "
                "FROM user_brokerage_roles ubr "
                "JOIN brokerages b ON b.brokerage_id = ubr.brokerage_id "
                "WHERE ubr.user_id = $1::uuid "
                "ORDER BY b.tenant_id, ubr.role",
                user_id,
            )
        return [BrokerageRole(**dict(r)) for r in rows]

    async def list_client_links_for_user(self, pool, user_id):
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT user_id::text AS user_id, tenant_id::text AS tenant_id, "
                "       client_id::text AS client_id, role "
                "FROM user_client_links WHERE user_id = $1::uuid "
                "ORDER BY tenant_id, client_id, role",
                user_id,
            )
        return [ClientLink(**dict(r)) for r in rows]


def _user_from_row(row) -> User:
    return User(
        user_id=row["user_id"], email=row["email"],
        display_name=row["display_name"], kind=row["kind"],
        password_hash=row["password_hash"],
        mfa_secret=row["mfa_secret"],
    )


def _tenant_from_row(row) -> Tenant:
    return Tenant(
        tenant_id=row["tenant_id"], slug=row["slug"],
        display_name=row["display_name"], genesis_hash=row["genesis_hash"],
    )


# ---- Public dispatcher functions -------------------------------------
async def read_session(store, session_id) -> Optional[Session]:
    return await _dispatch(store).read_session(store, session_id)


async def create_session(
    store, *, user_id, tenant_id, active_role,
    brokerage_id=None, client_id=None, mfa_verified=False,
    ttl: timedelta = SESSION_TTL,
) -> Session:
    return await _dispatch(store).create_session(
        store, user_id=user_id, tenant_id=tenant_id,
        active_role=active_role, brokerage_id=brokerage_id,
        client_id=client_id, mfa_verified=mfa_verified, ttl=ttl,
    )


async def revoke_session(store, session_id) -> None:
    await _dispatch(store).revoke_session(store, session_id)


async def mark_mfa_verified(store, session_id) -> None:
    await _dispatch(store).mark_mfa_verified(store, session_id)


async def update_active_context(
    store, session_id, *, tenant_id, client_id, active_role,
) -> None:
    await _dispatch(store).update_active_context(
        store, session_id, tenant_id=tenant_id,
        client_id=client_id, active_role=active_role,
    )


async def find_user_by_email(store, email) -> Optional[User]:
    return await _dispatch(store).find_user_by_email(store, email)


async def find_user_by_id(store, user_id) -> Optional[User]:
    return await _dispatch(store).find_user_by_id(store, user_id)


async def set_mfa_secret(store, user_id: str, secret: str) -> None:
    """Persist a fresh MFA secret for a user. R5a-1: identity-backend
    aware; Postgres path is a raw UPDATE, Mongo path goes through the
    tenant-safe wrapper under the user's tenant ctx (caller enters it)."""
    await _dispatch(store).set_mfa_secret(store, user_id, secret)


async def find_tenant_by_slug(store, slug) -> Optional[Tenant]:
    return await _dispatch(store).find_tenant_by_slug(store, slug)


async def find_tenant_by_id(store, tenant_id) -> Optional[Tenant]:
    return await _dispatch(store).find_tenant_by_id(store, tenant_id)


async def list_all_tenants(store) -> list[Tenant]:
    return await _dispatch(store).list_all_tenants(store)


async def list_brokerage_roles_for_user(store, user_id) -> list[BrokerageRole]:
    return await _dispatch(store).list_brokerage_roles_for_user(store, user_id)


async def list_client_links_for_user(store, user_id) -> list[ClientLink]:
    return await _dispatch(store).list_client_links_for_user(store, user_id)


# ---- R5a-1 helpers: pre-context lookups for auth routes --------------
async def find_user_by_email_across_tenants(store, email: str):
    """Cross-tenant user lookup for auth flows. Returns (User, tenant_id)
    or (None, None). Postgres path is a global lookup (users are
    tenant-agnostic in that schema); Mongo path iterates tenants."""
    if _is_pg_store(store):
        user = await find_user_by_email(store, email)
        if user is None:
            return None, None
        # Under Postgres, users carry no tenant_id — pick the first
        # tenant the user has any role/link in as the "home" tenant
        # for downstream auth events.
        brokerages = await list_brokerage_roles_for_user(store, user.user_id)
        if brokerages:
            return user, brokerages[0].tenant_id
        links = await list_client_links_for_user(store, user.user_id)
        if links:
            return user, links[0].tenant_id
        return user, None
    from app.kernel import identity_mongo
    return await identity_mongo.find_user_by_email_across_tenants(store, email)


async def list_brokerage_roles_across_tenants(store, user_id: str):
    if _is_pg_store(store):
        return await list_brokerage_roles_for_user(store, user_id)
    from app.kernel import identity_mongo
    return await identity_mongo.list_brokerage_roles_across_tenants(store, user_id)


async def list_client_links_across_tenants(store, user_id: str):
    if _is_pg_store(store):
        return await list_client_links_for_user(store, user_id)
    from app.kernel import identity_mongo
    return await identity_mongo.list_client_links_across_tenants(store, user_id)


__all__ = [
    "BrokerageRole", "COOKIE_NAME", "ClientLink",
    "IdentityBackendMismatch", "SESSION_TTL",
    "Session", "Tenant", "User", "active_backend",
    "create_session", "find_tenant_by_id", "find_tenant_by_slug",
    "find_user_by_email", "find_user_by_id",
    "list_all_tenants", "list_brokerage_roles_for_user",
    "list_client_links_for_user", "mark_mfa_verified",
    "read_session", "revoke_session", "update_active_context",
]

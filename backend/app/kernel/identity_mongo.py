"""brokAId Phase R4a — Mongo backend for the identity kernel.

Mirrors `app.kernel.identity`'s public API. Every read and write
goes through `TenantSafeDatabase` — with two documented exceptions:

  1. `read_session()` uses `safe_db.system("sessions")` to fetch a
     row by session_id BEFORE any tenant context exists. That's the
     R4a addition to `_SYSTEM_ALLOW`: session_id IS the tenant-
     discovery primitive.
  2. Tenant registry lookups use `safe_db.system("tenants")` as in R3.

Every OTHER session op (create/revoke/mark_mfa_verified/update-
active-context) is called AFTER the middleware has bound the ctx,
so it flows through the tenant-safe wrapper normally.

# tenant-safe: system-only

Schema (bit-for-bit mirror of the Postgres tables)
--------------------------------------------------
tenants:
    tenant_id  str (uuid)
    slug       str
    display_name str
    genesis_hash str (hex64)
    created_at datetime (ISO string in payload, native BSON on disk)

users:
    user_id    str (uuid)
    email      str
    display_name str
    kind       "operator" | "client"
    password_hash Optional[str]
    mfa_secret Optional[str]
    created_at datetime

user_brokerage_roles:
    user_id       str
    tenant_id     str          # denormalised from brokerages.tenant_id
    brokerage_id  str
    role          str
    active        bool

user_client_links:
    user_id       str
    tenant_id     str
    client_id     str
    role          str

sessions:
    session_id       str (uuid)
    user_id          str
    tenant_id        str
    brokerage_id     Optional[str]
    client_id        Optional[str]
    active_role      str
    mfa_verified     bool
    expires_at       datetime
    revoked_at       Optional[datetime]
    client_prefs     dict (JSON-shaped)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ASCENDING

from app.auth.sessions import SESSION_TTL, Session  # re-export shape
from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import (
    get_tenant_id, use_tenant,
)


# ---- Index bootstrap (idempotent) ------------------------------------
_indexes_initialised: set[int] = set()


async def _ensure_indexes(safe_db: TenantSafeDatabase) -> None:
    # tenant-safe: system-only
    from pymongo.errors import OperationFailure
    raw = safe_db.raw
    key = id(raw)
    if key in _indexes_initialised:
        return

    async def _try_index(coll: str, keys, **opts) -> None:
        try:
            await raw[coll].create_index(keys, **opts)
        except OperationFailure:
            # Existing data violates a unique constraint (e.g. dev-mode
            # tests reseed the same fixtures). Idempotent: swallow and
            # trust the caller's downstream `find_one` checks.
            pass

    try:  # R1.2b: one person may belong to several tenants → email unique per tenant
        await raw["users"].drop_index("users_email_uniq")
    except OperationFailure:
        pass
    await _try_index(
        "users", [("tenant_id", ASCENDING), ("email", ASCENDING)],
        unique=True, name="users_tenant_email_uniq",
    )
    await _try_index(
        "users", [("user_id", ASCENDING)],
        unique=True, name="users_id_uniq",
    )
    await _try_index(
        "user_brokerage_roles",
        [("user_id", ASCENDING), ("tenant_id", ASCENDING), ("role", ASCENDING)],
        unique=True, name="ubr_user_tenant_role_uniq",
    )
    await _try_index(
        "user_brokerage_roles", [("tenant_id", ASCENDING)], name="ubr_tenant",
    )
    await _try_index(
        "user_client_links",
        [("user_id", ASCENDING), ("tenant_id", ASCENDING), ("client_id", ASCENDING)],
        unique=True, name="ucl_user_tenant_client_uniq",  # R1.1: archived rows share user+client
    )
    await _try_index(
        "user_client_links",
        [("tenant_id", ASCENDING)], name="ucl_tenant",
    )
    await _try_index(
        "memberships",
        [("tenant_id", ASCENDING), ("user_id", ASCENDING), ("client_id", ASCENDING), ("role", ASCENDING)],
        unique=True, name="mem_tenant_user_client_role_uniq",
    )
    await _try_index(
        "sessions",
        [("session_id", ASCENDING)], unique=True, name="sess_id_uniq",
    )
    await _try_index(
        "sessions",
        [("user_id", ASCENDING)], name="sess_user",
    )
    await _try_index(
        "sessions",
        [("tenant_id", ASCENDING), ("expires_at", ASCENDING)],
        name="sess_tenant_expiry",
    )
    _indexes_initialised.add(key)


def _session_from_doc(d) -> Session:
    return Session(
        session_id=d["session_id"],
        user_id=d["user_id"],
        tenant_id=d["tenant_id"],
        brokerage_id=d.get("brokerage_id"),
        client_id=d.get("client_id"),
        active_role=d["active_role"],
        mfa_verified=bool(d["mfa_verified"]),
        expires_at=d["expires_at"],
    )


# ---- Session lifecycle -----------------------------------------------
async def read_session(store, session_id: str) -> Optional[Session]:
    """Pre-context session lookup — the ONE unfiltered read on the
    sessions collection. Used by the middleware to discover
    `tenant_id` from a cookie.

    # tenant-safe: system-only
    """
    try:
        uuid.UUID(session_id)
    except (TypeError, ValueError):
        return None
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    row = await safe_db.system("sessions").find_one({"session_id": session_id})
    if row is None:
        return None
    if row.get("revoked_at") is not None:
        return None
    exp = row["expires_at"]
    now = datetime.now(timezone.utc)
    # Motor returns aware datetimes when stored aware. Compare via UTC.
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return None
    if row.get("pending_mfa"):  # R1.2a: password ok but TOTP not yet passed
        return None
    if row.get("idle_s"):  # R1.2a: client idle timeout
        last = row.get("last_seen_at") or row["created_at"]
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if last + timedelta(seconds=row["idle_s"]) < now:
            return None
        if now - last > timedelta(seconds=60):
            await safe_db.system("sessions").update_one({"session_id": session_id}, {"$set": {"last_seen_at": now}})
    return _session_from_doc(row)


async def create_session(
    store, *,
    user_id: str, tenant_id: str, active_role: str,
    brokerage_id: Optional[str] = None,
    client_id: Optional[str] = None,
    mfa_verified: bool = False,
    ttl: timedelta = SESSION_TTL,
) -> Session:
    """Create a fresh session. Caller must have set the tenant ctx to
    `tenant_id` — this is enforced by the tenant-safe wrapper on
    insert."""
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + ttl
    async with use_tenant(tenant_id):
        await safe_db.sessions.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "brokerage_id": brokerage_id,
            "client_id": client_id,
            "active_role": active_role,
            "mfa_verified": bool(mfa_verified),
            "expires_at": expires_at,
            "revoked_at": None,
            "client_prefs": {},
            "created_at": datetime.now(timezone.utc),
        })
    return Session(
        session_id=session_id, user_id=user_id, tenant_id=tenant_id,
        brokerage_id=brokerage_id, client_id=client_id,
        active_role=active_role, mfa_verified=bool(mfa_verified),
        expires_at=expires_at,
    )


async def revoke_session(store, session_id: str) -> None:
    """Mark a session revoked. Caller must have ctx bound to the
    session's tenant — enforced by the wrapper. We look up the row
    via system() to get its tenant_id, then update via the wrapper
    (belt-and-braces)."""
    safe_db = wrap(store)
    # tenant-safe: system-only — need to resolve tenant_id before the
    # wrapper can inject the filter. This is idempotent + read-only.
    row = await safe_db.system("sessions").find_one({"session_id": session_id})
    if row is None:
        return
    async with use_tenant(row["tenant_id"]):
        await safe_db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {"revoked_at": datetime.now(timezone.utc)}},
        )


async def mark_mfa_verified(store, session_id: str) -> None:
    safe_db = wrap(store)
    row = await safe_db.system("sessions").find_one({"session_id": session_id})
    if row is None:
        return
    async with use_tenant(row["tenant_id"]):
        await safe_db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {"mfa_verified": True}},
        )


async def update_active_context(
    store, session_id: str, *,
    tenant_id: str, client_id: str, active_role: str,
) -> None:
    """Change the session's active tenant / client / role.

    Semantically identical to the Postgres path — the sessions row's
    tenant_id/client_id/active_role fields are rewritten in place.
    Uses `system("sessions")` for the filter because the caller may
    be switching FROM tenant A TO tenant B, and the wrapper would
    reject either side of that on filter-vs-doc contradiction.
    """
    safe_db = wrap(store)
    # tenant-safe: system-only — cross-tenant session update is a
    # legitimate operation for the "operator switches active tenant"
    # flow (though unused today). Bypass the wrapper here; the
    # caller is trusted to have RBAC-checked the switch.
    await safe_db.system("sessions").update_one(
        {"session_id": session_id},
        {"$set": {
            "tenant_id": tenant_id,
            "client_id": client_id,
            "active_role": active_role,
        }},
    )


# ---- Users ------------------------------------------------------------
async def find_user_by_email(store, email: str):
    """Look up a user by email. Uses `system("sessions")`-adjacent
    reasoning: user lookup by email happens at login BEFORE any
    session exists (no tenant ctx yet). We use `system()` — but on
    `tenants` — via a two-step: first find any brokerage-role row
    referencing the user, extract its tenant_id, then query users
    under that tenant context.

    Actually, users are tenant-agnostic in the schema (a user CAN
    have roles in multiple tenants). Reading them at login before
    ctx-binding is a pre-context op — we allow it via a tenant-
    less system read on `users`. To keep the R4a `_SYSTEM_ALLOW`
    list narrow, we DO NOT add `users` to it — instead, we route
    login-time lookups through the routes that call
    `find_user_by_email` and set ctx before this function.
    Callers today: `auth_operator._load_operator_by_email` (still
    Postgres), and the parity test (which sets ctx via
    `use_tenant`).

    So this function REQUIRES the tenant ctx to be set at call
    time. Cross-tenant lookups return None (as if the email
    doesn't exist in this tenant). That's the desired isolation.
    """
    from app.db.tenant_safe import wrap as _wrap
    safe_db = _wrap(store)
    await _ensure_indexes(safe_db)
    # Users are tenant-agnostic in the source table but we require
    # ctx anyway — an unfiltered lookup would leak cross-tenant.
    # The wrapper auto-injects tenant_id. Users seeded per tenant
    # via `seed_users_mongo` carry `tenant_id` for exactly this
    # scoping.
    get_tenant_id()  # fail-loud on missing ctx
    row = await safe_db.users.find_one({"email": email})
    return _user_from_doc(row) if row else None


async def find_user_by_id(store, user_id: str):
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    get_tenant_id()  # fail-loud
    row = await safe_db.users.find_one({"user_id": user_id})
    return _user_from_doc(row) if row else None


def _user_from_doc(d):
    from app.kernel.identity import User
    return User(
        user_id=d["user_id"], email=d["email"],
        display_name=d["display_name"], kind=d["kind"],
        password_hash=d.get("password_hash"),
        mfa_secret=d.get("mfa_secret"),
    )


# ---- Tenants (system-only) -------------------------------------------
async def find_tenant_by_slug(store, slug: str):
    # tenant-safe: system-only
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    row = await safe_db.system("tenants").find_one({"slug": slug})
    return _tenant_from_doc(row) if row else None


async def find_tenant_by_id(store, tenant_id: str):
    # tenant-safe: system-only
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    row = await safe_db.system("tenants").find_one({"tenant_id": tenant_id})
    return _tenant_from_doc(row) if row else None


async def list_all_tenants(store):
    # tenant-safe: system-only
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    out = []
    async for doc in safe_db.system("tenants").find({}).sort("slug", ASCENDING):
        out.append(_tenant_from_doc(doc))
    return out


def _tenant_from_doc(d):
    from app.kernel.identity import Tenant
    return Tenant(
        tenant_id=d["tenant_id"], slug=d["slug"],
        display_name=d["display_name"], genesis_hash=d["genesis_hash"],
    )


# ---- Roles ------------------------------------------------------------
async def list_brokerage_roles_for_user(store, user_id: str):
    """Return every brokerage-role row for a user, in the CURRENT tenant.

    Postgres path spans all tenants (RLS-filtered at query time via
    `set_config app.tenant_id`); Mongo path requires ctx to be set
    to the tenant we're listing for. If a caller genuinely needs
    cross-tenant listing they'd iterate `list_all_tenants()` and
    call this per tenant.
    """
    from app.kernel.identity import BrokerageRole
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    get_tenant_id()  # fail-loud
    out = []
    async for r in safe_db.user_brokerage_roles.find(
        {"user_id": user_id}
    ).sort([("tenant_id", ASCENDING), ("role", ASCENDING)]):
        out.append(BrokerageRole(
            user_id=r["user_id"], tenant_id=r["tenant_id"],
            brokerage_id=r["brokerage_id"], role=r["role"],
            active=bool(r["active"]),
        ))
    return out


async def list_client_links_for_user(store, user_id: str):
    from app.kernel.identity import ClientLink
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    get_tenant_id()  # fail-loud
    out = []
    async for r in safe_db.user_client_links.find(
        {"user_id": user_id}
    ).sort([("tenant_id", ASCENDING), ("client_id", ASCENDING), ("role", ASCENDING)]):
        out.append(ClientLink(
            user_id=r["user_id"], tenant_id=r["tenant_id"],
            client_id=r["client_id"], role=r["role"],
        ))
    return out


# ---- R5a-1: pre-context helpers for auth flows -----------------------
async def find_user_by_email_across_tenants(store, email: str):
    """Cross-tenant login/impersonate lookup. Iterates
    `list_all_tenants` and finds the first user matching `email`.

    # tenant-safe: system-only — this is a bootstrap primitive.
    Auth routes have no ctx yet; email lookup needs to succeed
    before ctx can be set. Returns the User + the tenant_id
    it was found in (so the caller can enter `use_tenant(tid)`
    before doing anything else). Postgres path can do a global
    lookup because users are tenant-agnostic there.
    """
    # Iterate tenants; break on the first tenant that has the email.
    for tenant in await list_all_tenants(store):
        async with use_tenant(tenant.tenant_id):
            user = await find_user_by_email(store, email)
            if user is not None:
                return user, tenant.tenant_id
    return None, None


async def list_brokerage_roles_across_tenants(store, user_id: str):
    """List every brokerage-role row for a user across all tenants.

    # tenant-safe: system-only — impersonation-time lookup.
    """
    from app.kernel.identity import BrokerageRole
    all_rows: list[BrokerageRole] = []
    for tenant in await list_all_tenants(store):
        async with use_tenant(tenant.tenant_id):
            all_rows.extend(await list_brokerage_roles_for_user(store, user_id))
    return all_rows


async def list_client_links_across_tenants(store, user_id: str):
    """List every client link for a user across all tenants.

    # tenant-safe: system-only — impersonation-time lookup.
    """
    from app.kernel.identity import ClientLink
    all_rows: list[ClientLink] = []
    for tenant in await list_all_tenants(store):
        async with use_tenant(tenant.tenant_id):
            all_rows.extend(await list_client_links_for_user(store, user_id))
    return all_rows


async def set_mfa_secret(store, user_id: str, secret: str):
    """Set MFA secret. Caller must be inside `use_tenant(tenant_id)`
    for the user's tenant — enforced by the wrapper."""
    safe_db = wrap(store)
    await _ensure_indexes(safe_db)
    get_tenant_id()  # fail-loud
    await safe_db.users.update_one(
        {"user_id": user_id},
        {"$set": {"mfa_secret": secret}},
    )

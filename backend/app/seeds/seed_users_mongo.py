"""Phase R4a — Mongo variant of the dev-users seed.

Byte-for-byte mirror of `app.seeds.seed_users` but writes to Mongo
collections through the tenant-safe wrapper. Idempotent — a second
call is a no-op (unique indexes + DuplicateKeyError caught).

Fictional data only. Same emails, same display names, same roles as
the Postgres seed — the parity test asserts every identity function
returns byte-identical shapes under both backends for these fixtures.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.kernel.trail_mongo import DuplicateIdempotencyKey

from app.auth import passwords
from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel import trail_mongo
from app.seeds.seed_users import (
    CLIENTS, DEV_PASSWORD, FICTIONAL_CLIENT_ID,
    OPERATORS, TENANT_NAME, TENANT_SLUG,
    BROKERAGE_SLUG, BROKERAGE_NAME, LICENSEE,
    LEGACY_TENANT_SLUG,
)


async def _ensure_tenant_mongo(safe_db) -> str:
    """Idempotent tenant registry insert + genesis event append."""
    # tenant-safe: system-only — pre-context tenant registry read.
    row = await safe_db.system("tenants").find_one({"slug": TENANT_SLUG})
    if row:
        return row["tenant_id"]
    new_tid = str(uuid.uuid4())
    async with use_tenant(new_tid):
        try:
            tenant, _ = await trail_mongo.create_tenant(
                safe_db, TENANT_SLUG, TENANT_NAME,
                tenant_id_override=new_tid,
            )
            return tenant.tenant_id
        except (DuplicateKeyError, ValueError):
            # Lost the race — read the winner.
            row = await safe_db.system("tenants").find_one({"slug": TENANT_SLUG})
            assert row is not None
            return row["tenant_id"]


async def _repoint_legacy_logins(db, tenant_id: str) -> int:
    """R0.6 G3: move fictional demo logins (*.example) off the legacy tenant."""
    # tenant-safe: system-only — cross-tenant user repoint, seed-time only.
    legacy = await db.tenants.find_one({"slug": LEGACY_TENANT_SLUG})
    if not legacy or legacy["tenant_id"] == tenant_id:
        return 0
    res = await db.users.update_many(
        {"tenant_id": legacy["tenant_id"], "email": {"$regex": r"\.example$"}},
        {"$set": {"tenant_id": tenant_id, "repointed_from": legacy["tenant_id"]}},
    )
    moved = [u["user_id"] async for u in db.users.find({"repointed_from": legacy["tenant_id"]}, {"user_id": 1})]
    await db.user_brokerage_roles.update_many(
        {"tenant_id": legacy["tenant_id"], "user_id": {"$in": moved}}, {"$set": {"active": False}})
    await db.user_client_links.update_many(
        {"tenant_id": legacy["tenant_id"], "user_id": {"$in": moved}},
        {"$set": {"tenant_id": f"archived:{legacy['tenant_id']}"}})
    return res.modified_count


async def _ensure_brokerage_mongo(safe_db, tenant_id: str) -> str:
    async with use_tenant(tenant_id):
        # Upsert on slug. The tenant-safe wrapper injects tenant_id
        # on both sides — find AND insert.
        existing = await safe_db.brokerages.find_one({"slug": BROKERAGE_SLUG})
        if existing:
            return existing["brokerage_id"]
        brokerage_id = str(uuid.uuid4())
        try:
            await safe_db.brokerages.insert_one({
                "brokerage_id": brokerage_id,
                "tenant_id": tenant_id,
                "slug": BROKERAGE_SLUG,
                "display_name": BROKERAGE_NAME,
                "licensee": LICENSEE,
                "created_at": datetime.now(timezone.utc),
            })
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            existing = await safe_db.brokerages.find_one({"slug": BROKERAGE_SLUG})
            assert existing is not None
            return existing["brokerage_id"]
        return brokerage_id


async def _ensure_operator_mongo(
    safe_db, tenant_id: str, brokerage_id: str, spec: dict,
) -> str:
    async with use_tenant(tenant_id):
        # `users` is tenant-scoped in the Mongo model — the wrapper
        # injects tenant_id on filter and doc. A different tenant
        # with the same email would still work; here we look up
        # under the current ctx first.
        existing = await safe_db.users.find_one({"email": spec["email"]})
        if existing:
            user_id = existing["user_id"]
        else:
            user_id = str(uuid.uuid4())
            try:
                await safe_db.users.insert_one({
                    "user_id": user_id,
                    "email": spec["email"],
                    "display_name": spec["display_name"],
                    "kind": "operator",
                    "password_hash": passwords.hash_password(DEV_PASSWORD),
                    "mfa_secret": None,
                    "created_at": datetime.now(timezone.utc),
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                # Lost the race.
                existing = await safe_db.users.find_one({"email": spec["email"]})
                assert existing is not None
                user_id = existing["user_id"]

        # Role — idempotent: check first (no unique index on this collection).
        try:
            if await safe_db.user_brokerage_roles.find_one(
                    {"user_id": user_id, "role": spec["role"]}):
                return user_id
            await safe_db.user_brokerage_roles.insert_one({
                "user_id": user_id,
                "tenant_id": tenant_id,
                "brokerage_id": brokerage_id,
                "role": spec["role"],
                "active": True,
            })
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass
        return user_id


async def _ensure_client_mongo(safe_db, tenant_id: str, spec: dict) -> str:
    async with use_tenant(tenant_id):
        existing = await safe_db.users.find_one({"email": spec["email"]})
        if existing:
            user_id = existing["user_id"]
        else:
            user_id = str(uuid.uuid4())
            try:
                await safe_db.users.insert_one({
                    "user_id": user_id,
                    "email": spec["email"],
                    "display_name": spec["display_name"],
                    "kind": "client",
                    "password_hash": None,
                    "mfa_secret": None,
                    "created_at": datetime.now(timezone.utc),
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                existing = await safe_db.users.find_one({"email": spec["email"]})
                assert existing is not None
                user_id = existing["user_id"]

        try:
            await safe_db.user_client_links.insert_one({
                "user_id": user_id,
                "tenant_id": tenant_id,
                "client_id": str(FICTIONAL_CLIENT_ID),
                "role": spec["role"],
            })
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass
        return user_id


async def seed_mongo(db: AsyncIOMotorDatabase) -> dict:
    """Seed dev users into Mongo. Byte-identical fixtures to
    `seed_users.seed()` for the parity test."""
    safe_db = wrap(db)
    tenant_id = await _ensure_tenant_mongo(safe_db)
    await _repoint_legacy_logins(db, tenant_id)
    brokerage_id = await _ensure_brokerage_mongo(safe_db, tenant_id)
    ops = []
    principal_id = None
    for s in OPERATORS:
        uid = await _ensure_operator_mongo(safe_db, tenant_id, brokerage_id, s)
        ops.append({"user_id": uid, **s})
        if s.get("role") == "principal" and principal_id is None:
            principal_id = uid
    cls = []
    for s in CLIENTS:
        uid = await _ensure_client_mongo(safe_db, tenant_id, s)
        cls.append({"user_id": uid, **s})
    return {
        "tenant_id": tenant_id,
        "brokerage_id": brokerage_id,
        "client_entity_id": str(FICTIONAL_CLIENT_ID),
        "operators": ops,
        "clients": cls,
        "principal_id": principal_id,
    }

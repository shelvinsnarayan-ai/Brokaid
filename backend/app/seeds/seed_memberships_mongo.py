"""R0.4 — memberships for Kai's client: owner (Kai) + bookkeeper. Idempotent."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap
from app.kernel.authority import ROLE_AUTHORITY
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.seeds.seed_users import FICTIONAL_CLIENT_ID

OWNER_EMAIL = "owner@fictionalclient.example"
BOOKKEEPER = {"email": "bookkeeper@fictionalclient.example",
              "display_name": "Bea Bookkeeper", "role": "client_contact"}


async def _ensure_user(safe, tenant_id: str) -> str:
    u = await safe.users.find_one({"email": BOOKKEEPER["email"]})
    if u:
        if not await safe.user_client_links.find_one({"user_id": u["user_id"], "client_id": str(FICTIONAL_CLIENT_ID)}):
            await safe.user_client_links.insert_one({
                "user_id": u["user_id"], "tenant_id": tenant_id,
                "client_id": str(FICTIONAL_CLIENT_ID), "role": BOOKKEEPER["role"],
            })
        return u["user_id"]
    user_id = str(uuid.uuid4())
    try:
        await safe.users.insert_one({
            "user_id": user_id, "email": BOOKKEEPER["email"],
            "display_name": BOOKKEEPER["display_name"], "kind": "client",
            "password_hash": None, "mfa_secret": None, "fictional": True,
            "created_at": datetime.now(timezone.utc),
        })
        await safe.user_client_links.insert_one({
            "user_id": user_id, "tenant_id": tenant_id,
            "client_id": str(FICTIONAL_CLIENT_ID), "role": BOOKKEEPER["role"],
        })
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        user_id = (await safe.users.find_one({"email": BOOKKEEPER["email"]}))["user_id"]
    return user_id


async def seed_mongo(db: AsyncIOMotorDatabase, tenant_id: str) -> None:
    safe = wrap(db)
    client_id = str(FICTIONAL_CLIENT_ID)
    async with use_tenant(tenant_id):
        owner = await safe.users.find_one({"email": OWNER_EMAIL})
        bk_id = await _ensure_user(safe, tenant_id)
        for user_id, role in ((owner["user_id"], "owner"), (bk_id, "bookkeeper")):
            if await safe.memberships.find_one({"user_id": user_id, "client_id": client_id}):
                continue
            await safe.memberships.insert_one({
                "membership_id": str(uuid.uuid4()), "client_id": client_id,
                "user_id": user_id, "role": role, "authority": ROLE_AUTHORITY[role],
                "created_at": datetime.now(timezone.utc), "created_by": "seed",
                "revoked_at": None, "revoked_by": None,
            })

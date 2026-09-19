"""Phase R5c-2a — Mongo variant of phase-5 client seed.

Byte-for-byte mirror of `app.seeds.seed_client` — inserts the
Harbour Marine Services Pty Ltd client under `use_tenant(tid)` and
emits `ClientEntityCreated` via `trail_dispatch.append_via_dispatch`.
Idempotent via `find_one` guard + DuplicateKeyError catch on both
the client doc and the ledger event.
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.kernel.trail_mongo import DuplicateIdempotencyKey

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.seeds.seed_client import (
    CLIENT_ID, CLIENT_NAME, DOMICILE, INDUSTRY, JURISDICTION,
)


async def seed_mongo(db: AsyncIOMotorDatabase, tenant_id: str) -> str:
    safe = wrap(db)
    async with use_tenant(tenant_id):
        existing = await safe.clients.find_one({"client_id": CLIENT_ID})
        if not existing:
            try:
                await safe.clients.insert_one({
                    "client_id": CLIENT_ID,
                    "display_name": CLIENT_NAME,
                    "industry": INDUSTRY,
                    "domicile": DOMICILE,
                    "jurisdiction_code": JURISDICTION,
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                pass
    try:
        await append_via_dispatch(
            db, tenant_id, "ClientEntityCreated",
            actor={"kind": "system", "id": "seed", "name": "phase5-seed"},
            payload={
                "client_id": CLIENT_ID,
                "display_name": CLIENT_NAME,
                "industry": INDUSTRY,
                "domicile": DOMICILE,
                "jurisdiction_code": JURISDICTION,
                "fictional": True,
            },
            idempotency_key=f"seed:client:{CLIENT_ID}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return CLIENT_ID
